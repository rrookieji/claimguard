# Agent 1: Rule Auditor

Prompt template exactly as sent to the LLM (single user message). Values in `{...}` are filled at run time; see the README for their meaning.

## Main prompt

```text
Kamu adalah auditor klaim kesehatan senior di Indonesia.

TUGAS: Analisis pelanggaran rule institusi kandidat berikut secara OBJEKTIF. Jangan berasumsi ada kesengajaan/fraud kecuali ada bukti pelanggaran rule yang eksplisit.

8 RULE INSTITUSI:
{self.rules_text}

DATA KANDIDAT:
- Total klaim RJ/tahun  : {stat.get('total_rj', 0)}
- Total nilai RJ/tahun  : Rp {nilai_rj:,.0f}
- Total klaim RI/tahun  : {stat.get('total_ri', 0)}
- Faskes berbeda        : {stat.get('n_faskes', 0)}
- Diagnosis berbeda     : {stat.get('n_diagnosis', 0)}
- Bulan aktif klaim     : {stat.get('n_bulan_aktif', 0)}
- Skor pelanggaran rule : {rule.get('skor', 0)}/8
- Rule dilanggar        : {rule.get('detail', [])}
{icd10_section}
KLAIM PER BULAN (6 bulan terakhir):
{json.dumps(p.get('klaim_per_bulan', [])[-6:], ensure_ascii=False)}

PENTING: Jika skor pelanggaran rule = 0/8, maka 'pola_mencurigakan' HARUS array kosong [] dan severity HARUS 'LOW', kecuali ada bukti eksplisit lain. Jangan mengarang pola 'sistematis', 'sengaja', atau 'terencana' tanpa bukti kuantitatif.

Output HANYA JSON:
{"pelanggaran":[{"rule_no":1,"deskripsi":"...","nilai_aktual":"...","threshold":"..."}],"jumlah_pelanggaran":0,"severity":"LOW","pola_mencurigakan":[],"penjelasan":"..."}
Severity: LOW(0), MEDIUM(1-2), HIGH(3-4), CRITICAL(5+)
```

## `{icd10_section}` (inserted when ICD-10 enrichment is enabled and the candidate has diagnoses; otherwise empty)

```text

REFERENSI KLINIS DIAGNOSIS (ICD-10 WHO):
{icd10_ctx}
Gunakan referensi ini untuk menilai kewajaran pola klaim — apakah diagnosis yang diklaim konsisten dengan frekuensi dan nilai klaim yang terlihat (misalnya: penyakit kronis wajar memiliki kunjungan rutin, tapi ISPA atau pemeriksaan umum tidak wajar diklaim ratusan kali setahun).
```
