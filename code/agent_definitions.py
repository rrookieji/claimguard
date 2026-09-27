"""
agent_definitions.py
=====================
Definisi arsitektur multi-agent (Rule Auditor, MCU Expert, Decision Maker)
dan single-agent, dipakai bersama oleh A06 (investigasi produksi) dan
A08 (ablation study) — supaya logika agent tidak diduplikasi di dua tempat.

Prompt-prompt di bawah disalin verbatim dari kode asli (A07b_ablation_study.ipynb)
tanpa perubahan logika, hanya direstrukturisasi jadi class untuk clean code.
"""

import json
import time
from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage


class AgentState(TypedDict):
    profil: dict
    skenario_id: str
    gunakan_mcu: bool
    gunakan_icd10: bool
    laporan_rule: Optional[dict]
    laporan_mcu: Optional[dict]
    laporan_final: Optional[dict]
    error: Optional[str]


def parse_json(text):
    """Ekstrak JSON dari respons LLM (menangani format list/dict/markdown code-fence)."""
    try:
        if isinstance(text, list):
            text = text[0].get('text', '') if text else ''
        if isinstance(text, dict):
            text = text.get('text', '')
        text = str(text).strip()
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0]
        elif '```' in text:
            text = text.split('```')[1].split('```')[0]
        return json.loads(text.strip())
    except Exception:
        return {'error': 'parse_failed', 'raw': str(text)[:200]}


def get_all_diagnoses(profil: dict) -> list:
    """Kumpulkan semua kode ICD-10 unik dari klaim_per_bulan, urutan terjaga."""
    diagnoses = []
    for bulan in profil.get('klaim_per_bulan', []):
        diagnoses.extend(bulan.get('diagnoses', []))
    return list(dict.fromkeys(d for d in diagnoses if d))


def get_icd10_context(diagnoses: list, icd10_map: dict) -> str:
    """Ubah daftar kode ICD-10 menjadi teks referensi klinis untuk prompt."""
    lines, seen = [], set()
    for kode in diagnoses:
        if not kode or str(kode).strip() in ('', 'nan', 'None'):
            continue
        kode = str(kode).strip().upper()
        if kode in seen:
            continue
        seen.add(kode)
        desc = icd10_map.get(kode)
        if not desc and len(kode) > 3:
            desc = icd10_map.get(kode[:3])
        lines.append(f'- {kode}: {desc}' if desc else f'- {kode}: (deskripsi tidak tersedia)')
    return '\n'.join(lines) if lines else 'Tidak ada diagnosis tercatat'


class AgentPipeline:
    """
    Kumpulan agent (Rule Auditor, MCU Expert, Decision Maker, Single-Agent)
    terikat ke satu instance LLM dan referensi ICD-10/rule institusional.
    """

    def __init__(self, llm, icd10_map: dict, rules_text: str):
        self.llm = llm
        self.icd10_map = icd10_map
        self.rules_text = rules_text
        self.app_multi = self._build_graph_multi()
        self.app_single = self._build_graph_single()

    def _invoke_llm(self, prompt: str) -> dict:
        resp = self.llm.invoke([HumanMessage(content=prompt)])
        return parse_json(resp.content)

    # ── Agent: Rule Auditor ────────────────────────────────────────────────
    def agent_rule_auditor(self, state: AgentState) -> dict:
        p = state['profil']
        stat = p.get('statistik', {})
        rule = p.get('rule_violation', {})
        nilai_rj = stat.get('nilai_rj', 0)
        diagnoses = get_all_diagnoses(p)

        icd10_section = ''
        if state.get('gunakan_icd10', False) and diagnoses:
            icd10_ctx = get_icd10_context(diagnoses, self.icd10_map)
            icd10_section = (
                f"\nREFERENSI KLINIS DIAGNOSIS (ICD-10 WHO):\n{icd10_ctx}\n"
                "Gunakan referensi ini untuk menilai kewajaran pola klaim — "
                "apakah diagnosis yang diklaim konsisten dengan frekuensi dan "
                "nilai klaim yang terlihat (misalnya: penyakit kronis wajar "
                "memiliki kunjungan rutin, tapi ISPA atau pemeriksaan umum "
                "tidak wajar diklaim ratusan kali setahun).\n"
            )

        prompt = (
            "Kamu adalah auditor klaim kesehatan senior di Indonesia.\n\n"
            "TUGAS: Analisis pelanggaran rule institusi kandidat berikut secara "
            "OBJEKTIF. Jangan berasumsi ada kesengajaan/fraud kecuali ada bukti "
            "pelanggaran rule yang eksplisit.\n\n"
            f"8 RULE INSTITUSI:\n{self.rules_text}\n\n"
            "DATA KANDIDAT:\n"
            f"- Total klaim RJ/tahun  : {stat.get('total_rj', 0)}\n"
            f"- Total nilai RJ/tahun  : Rp {nilai_rj:,.0f}\n"
            f"- Total klaim RI/tahun  : {stat.get('total_ri', 0)}\n"
            f"- Faskes berbeda        : {stat.get('n_faskes', 0)}\n"
            f"- Diagnosis berbeda     : {stat.get('n_diagnosis', 0)}\n"
            f"- Bulan aktif klaim     : {stat.get('n_bulan_aktif', 0)}\n"
            f"- Skor pelanggaran rule : {rule.get('skor', 0)}/8\n"
            f"- Rule dilanggar        : {rule.get('detail', [])}\n"
            f"{icd10_section}\n"
            "KLAIM PER BULAN (6 bulan terakhir):\n"
            + json.dumps(p.get('klaim_per_bulan', [])[-6:], ensure_ascii=False)
            + "\n\nPENTING: Jika skor pelanggaran rule = 0/8, maka "
            "'pola_mencurigakan' HARUS array kosong [] dan severity HARUS 'LOW', "
            "kecuali ada bukti eksplisit lain. Jangan mengarang pola 'sistematis', "
            "'sengaja', atau 'terencana' tanpa bukti kuantitatif.\n\n"
            "Output HANYA JSON:\n"
            '{"pelanggaran":[{"rule_no":1,"deskripsi":"...","nilai_aktual":"...","threshold":"..."}],'
            '"jumlah_pelanggaran":0,"severity":"LOW","pola_mencurigakan":[],"penjelasan":"..."}'
            "\nSeverity: LOW(0), MEDIUM(1-2), HIGH(3-4), CRITICAL(5+)"
        )
        try:
            laporan = self._invoke_llm(prompt)
            print(f'    R: {laporan.get("severity", "?")} ({laporan.get("jumlah_pelanggaran", "?")} rule)')
            return {'laporan_rule': laporan}
        except Exception as e:
            return {'laporan_rule': {'error': str(e)}, 'error': str(e)}

    # ── Agent: MCU Expert ───────────────────────────────────────────────────
    def agent_mcu_expert(self, state: AgentState) -> dict:
        if not state.get('gunakan_mcu', True):
            print('    M: SKIP (tanpa MCU)')
            return {'laporan_mcu': {'status': 'SKIPPED', 'alasan': 'Skenario tanpa MCU', 'severity': 'UNKNOWN'}}
        mcu = state['profil'].get('mcu', {})
        if not mcu.get('ada_data', False):
            print('    M: SKIP (tidak ada MCU)')
            return {'laporan_mcu': {'status': 'SKIPPED', 'alasan': 'Tidak ada data MCU', 'severity': 'UNKNOWN'}}

        mcu_detail = mcu.get('detail', {})
        lap_rule = state.get('laporan_rule') or {}
        diagnoses = get_all_diagnoses(state['profil'])[:10]
        prompt = (
            "Kamu adalah dokter spesialis penyakit dalam di Indonesia.\n\n"
            "TUGAS: Analisis KONSISTENSI antara diagnosis klaim dan hasil MCU "
            "secara berimbang — cari bukti MERINGANKAN maupun MEMBERATKAN.\n\n"
            f"DATA MCU TERAKHIR:\n{json.dumps(mcu_detail, ensure_ascii=False)}\n\n"
            f"DIAGNOSIS YANG DIKLAIM (ICD-10): {diagnoses}\n\n"
            f"KONTEKS RULE AUDITOR:\n- Severity: {lap_rule.get('severity', '?')}\n"
            f"- Pelanggaran: {lap_rule.get('jumlah_pelanggaran', 0)} rule\n\n"
            "PANDUAN INKONSISTENSI (mencurigakan):\n"
            "- E10-E14 (Diabetes) tapi gula darah/HbA1c normal -> INKONSISTEN\n"
            "- I10-I15 (Hipertensi) tapi tekanan darah normal -> INKONSISTEN\n"
            "- M10 (Gout) tapi asam urat normal -> INKONSISTEN\n"
            "- N18 (Ginjal) tapi kreatinin normal -> INKONSISTEN\n\n"
            "PANDUAN KONSISTENSI (tidak mencurigakan, WAJIB dicek):\n"
            "- E10-E14 (Diabetes) DAN gula darah/HbA1c abnormal -> KONSISTEN\n"
            "- I10-I15 (Hipertensi) DAN tekanan darah tinggi -> KONSISTEN\n"
            "- M10 (Gout) DAN asam urat tinggi -> KONSISTEN\n"
            "- N18 (Ginjal) DAN kreatinin abnormal -> KONSISTEN\n"
            "- Diagnosis tanpa parameter MCU (cedera, operasi, infeksi akut) -> NETRAL\n\n"
            "Jika SEMUA konsisten/netral, jumlah_inkonsistensi HARUS 0 dan "
            "severity HARUS 'LOW'. Sebutkan temuan konsistensi di 'penjelasan'.\n\n"
            "Output HANYA JSON:\n"
            '{"inkonsistensi":[{"diagnosis":"...","parameter_mcu":"...","nilai":"...","kesimpulan":"..."}],'
            '"jumlah_inkonsistensi":0,"severity":"LOW","penjelasan":"..."}'
            "\nSeverity: LOW(0), MEDIUM(1), HIGH(2), CRITICAL(3+)"
        )
        try:
            laporan = self._invoke_llm(prompt)
            print(f'    M: {laporan.get("severity", "?")} ({laporan.get("jumlah_inkonsistensi", "?")} inkonsistensi)')
            return {'laporan_mcu': laporan}
        except Exception as e:
            return {'laporan_mcu': {'error': str(e)}, 'error': str(e)}

    # ── Agent: Decision Maker (varian multi-agent) ──────────────────────────
    def agent_decision_maker_multi(self, state: AgentState) -> dict:
        stat = state['profil'].get('statistical_score', {})
        lap_rule = state.get('laporan_rule') or {}
        lap_mcu = state.get('laporan_mcu') or {}
        prob = stat.get('probabilitas', stat.get('skor_pelanggaran', stat.get('anomaly_score_min', 0)))
        prompt = (
            "Kamu adalah investigator fraud senior 10 tahun di klaim kesehatan korporasi Indonesia.\n\n"
            "LAPORAN RULE AUDITOR:\n"
            f"- Pelanggaran: {lap_rule.get('jumlah_pelanggaran', 0)}/8 rule\n"
            f"- Severity: {lap_rule.get('severity', '?')}\n"
            f"- Pola: {lap_rule.get('pola_mencurigakan', [])}\n"
            f"- Penjelasan: {lap_rule.get('penjelasan', '-')}\n\n"
            "LAPORAN MCU EXPERT:\n"
            f"- Status: {lap_mcu.get('status', 'ANALYZED')}\n"
            f"- Inkonsistensi: {lap_mcu.get('jumlah_inkonsistensi', 'N/A')}\n"
            f"- Severity: {lap_mcu.get('severity', '?')}\n"
            f"- Penjelasan: {lap_mcu.get('penjelasan', '-')}\n\n"
            f"SKOR STATISTIK:\n- Skor Layer 1: {prob}\n"
            f"- Skenario: {state.get('skenario_id', '?')}\n\n"
            "ATURAN KALIBRASI SKOR RISIKO (WAJIB DIIKUTI):\n"
            "- Jika pelanggaran rule = 0/8 DAN inkonsistensi MCU = 0: skor MAKSIMAL 5, "
            "verdict MAKSIMAL 'SUSPICIOUS', kecuali ada pola eksplisit yang tidak bisa "
            "dijelaskan kondisi medis wajar.\n"
            "- Jika pelanggaran rule >= 1: verdict dan skor boleh tinggi.\n"
            "- Jangan gunakan kata 'sengaja', 'terencana', 'sistematis' tanpa bukti rule eksplisit.\n\n"
            "Output HANYA JSON (nilai di bawah HANYA format, bukan jawaban default):\n"
            '{"verdict":"<FRAUD|SUSPICIOUS|NORMAL>","skor_risiko":"<0-10>","level_risiko":"<LOW|MEDIUM|HIGH>",'
            '"alasan_utama":["...","...","..."],"bukti_kuat":["..."],"rekomendasi":"...",'
            '"prioritas":"<TINGGI|SEDANG|RENDAH>","narasi":"Narasi 2-3 kalimat Bahasa Indonesia."}'
        )
        try:
            laporan = self._invoke_llm(prompt)
            print(f'    D: {laporan.get("verdict", "?")} ({laporan.get("skor_risiko", "?")}/10)')
            return {'laporan_final': laporan}
        except Exception as e:
            return {'laporan_final': {'error': str(e)}, 'error': str(e)}

    # ── Agent: Single-Agent (dipakai C1/C2 di ablation study) ──────────────
    def agent_single(self, state: AgentState) -> dict:
        p = state['profil']
        stat = p.get('statistik', {})
        rule = p.get('rule_violation', {})
        mcu = p.get('mcu', {})
        nilai_rj = stat.get('nilai_rj', 0)
        skor_layer1 = p.get('statistical_score', {})
        prob = skor_layer1.get('skor_pelanggaran', skor_layer1.get('anomaly_score_min', 0))
        diagnoses = get_all_diagnoses(p)

        icd10_section = ''
        if state.get('gunakan_icd10', False) and diagnoses:
            icd10_ctx = get_icd10_context(diagnoses, self.icd10_map)
            icd10_section = (
                f"\nREFERENSI KLINIS DIAGNOSIS (ICD-10 WHO):\n{icd10_ctx}\n"
                "Pertimbangkan apakah diagnosis ini wajar memiliki kunjungan "
                "berulang (penyakit kronis) atau tidak (ISPA, pemeriksaan umum).\n"
            )

        mcu_section = ''
        if state.get('gunakan_mcu', False) and mcu.get('ada_data', False):
            mcu_section = (
                f"\nDATA MCU TERAKHIR:\n{json.dumps(mcu.get('detail', {}), ensure_ascii=False)}\n"
                "Cek apakah diagnosis klaim konsisten dengan parameter MCU.\n"
            )

        prompt = (
            "Kamu adalah investigator fraud senior klaim kesehatan korporasi Indonesia.\n\n"
            "TUGAS: Analisis kandidat berikut secara komprehensif dan putuskan "
            "apakah pola klaimnya mencurigakan sebagai fraud.\n\n"
            f"8 RULE INSTITUSI:\n{self.rules_text}\n\n"
            "DATA KANDIDAT:\n"
            f"- Total klaim RJ/tahun  : {stat.get('total_rj', 0)}\n"
            f"- Total nilai RJ/tahun  : Rp {nilai_rj:,.0f}\n"
            f"- Total klaim RI/tahun  : {stat.get('total_ri', 0)}\n"
            f"- Faskes berbeda        : {stat.get('n_faskes', 0)}\n"
            f"- Diagnosis berbeda     : {stat.get('n_diagnosis', 0)}\n"
            f"- Bulan aktif klaim     : {stat.get('n_bulan_aktif', 0)}\n"
            f"- Skor pelanggaran rule : {rule.get('skor', 0)}/8\n"
            f"- Rule dilanggar        : {rule.get('detail', [])}\n"
            f"- Skor Layer 1          : {prob}\n"
            f"{icd10_section}"
            f"{mcu_section}"
            "KLAIM PER BULAN (6 bulan terakhir):\n"
            + json.dumps(p.get('klaim_per_bulan', [])[-6:], ensure_ascii=False)
            + "\n\nATURAN KALIBRASI:\n"
            "- Jika pelanggaran rule = 0/8: skor MAKSIMAL 5, verdict MAKSIMAL 'SUSPICIOUS'.\n"
            "- Jika pelanggaran rule >= 1: verdict dan skor boleh tinggi.\n"
            "- Jangan klaim 'sengaja' atau 'terencana' tanpa bukti eksplisit.\n\n"
            "Output HANYA JSON:\n"
            '{"verdict":"<FRAUD|SUSPICIOUS|NORMAL>","skor_risiko":"<0-10>","level_risiko":"<LOW|MEDIUM|HIGH>",'
            '"alasan_utama":["...","...","..."],"bukti_kuat":["..."],"rekomendasi":"...",'
            '"prioritas":"<TINGGI|SEDANG|RENDAH>","narasi":"Narasi 2-3 kalimat Bahasa Indonesia."}'
        )
        try:
            laporan = self._invoke_llm(prompt)
            print(f'    SA: {laporan.get("verdict", "?")} ({laporan.get("skor_risiko", "?")}/10)')
            return {'laporan_final': laporan}
        except Exception as e:
            return {'laporan_final': {'error': str(e)}, 'error': str(e)}

    # ── Routing dan Graph ────────────────────────────────────────────────────
    def _routing_mcu(self, state: AgentState) -> str:
        if not state.get('gunakan_mcu', True):
            return 'skip'
        if not state['profil'].get('mcu', {}).get('ada_data', False):
            return 'skip'
        return 'ke_mcu'

    def _build_graph_multi(self):
        """Graph multi-agent: Rule Auditor -> MCU Expert (kondisional) -> Decision Maker."""
        g = StateGraph(AgentState)
        g.add_node('rule_auditor', self.agent_rule_auditor)
        g.add_node('mcu_expert', self.agent_mcu_expert)
        g.add_node('decision_maker', self.agent_decision_maker_multi)
        g.set_entry_point('rule_auditor')
        g.add_conditional_edges('rule_auditor', self._routing_mcu, {'ke_mcu': 'mcu_expert', 'skip': 'decision_maker'})
        g.add_edge('mcu_expert', 'decision_maker')
        g.add_edge('decision_maker', END)
        return g.compile()

    def _build_graph_single(self):
        """Graph single-agent: satu node, satu prompt komprehensif."""
        g = StateGraph(AgentState)
        g.add_node('single_agent', self.agent_single)
        g.set_entry_point('single_agent')
        g.add_edge('single_agent', END)
        return g.compile()

    # ── Entry point pemrosesan 1 kandidat, dengan retry rate-limit ─────────
    def proses_satu(self, profil: dict, skenario_id: str, gunakan_mcu: bool,
                     arsitektur: str, gunakan_icd10: bool, max_retry: int = 3) -> dict:
        state = AgentState(
            profil=profil, skenario_id=skenario_id, gunakan_mcu=gunakan_mcu,
            gunakan_icd10=gunakan_icd10, laporan_rule=None, laporan_mcu=None,
            laporan_final=None, error=None,
        )
        app = self.app_multi if arsitektur == 'multi' else self.app_single

        for attempt in range(max_retry):
            try:
                start = time.time()
                hasil = app.invoke(state)
                durasi = time.time() - start
                final = hasil.get('laporan_final') or {}
                return {
                    'member_no': profil['member_no'],
                    'nik_hash': profil.get('nik_hash', ''),
                    'skenario': skenario_id,
                    'gunakan_mcu': gunakan_mcu,
                    'gunakan_icd10': gunakan_icd10,
                    'arsitektur': arsitektur,
                    'verdict': final.get('verdict', '?'),
                    'skor_risiko': final.get('skor_risiko', 0),
                    'level_risiko': final.get('level_risiko', '?'),
                    'prioritas': final.get('prioritas', '?'),
                    'alasan': ' | '.join(final.get('alasan_utama', [])),
                    'rekomendasi': final.get('rekomendasi', ''),
                    'narasi': final.get('narasi', ''),
                    'lap_rule_sev': (hasil.get('laporan_rule') or {}).get('severity', '?'),
                    'lap_mcu_sev': (hasil.get('laporan_mcu') or {}).get('severity', '?'),
                    'durasi_detik': round(durasi, 1),
                    'error': hasil.get('error'),
                }
            except Exception as e:
                err_str = str(e)
                if 'rate' in err_str.lower() or '429' in err_str:
                    wait = (attempt + 1) * 60
                    print(f'    Rate limited, tunggu {wait} detik...')
                    time.sleep(wait)
                else:
                    return {'member_no': profil['member_no'], 'skenario': skenario_id, 'error': err_str, 'verdict': 'ERROR'}
        return {'member_no': profil['member_no'], 'skenario': skenario_id, 'error': 'Max retry exceeded', 'verdict': 'ERROR'}
