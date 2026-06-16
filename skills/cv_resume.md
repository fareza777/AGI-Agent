---
name: cv_resume
description: Build/update a CV or resume as polished docx tailored to a target role. USE WHEN: "buatkan CV", "resume", "daftar riwayat hidup", "tailor untuk lowongan X". NOT for cover letters (→ email_drafter) or proposals (→ proposal_writer).
status: active
version: 1
---
# CV / Resume Builder

When the user asks for a CV, resume, daftar riwayat hidup, or to tailor one
for a job application:

1. `recall` everything known about the user: work history, education, skills,
   achievements, contact info. Ask only for gaps — and `remember` each new
   fact they give (subject=user, predicates like work_history, education,
   skills) so the next CV needs zero re-asking.
2. If targeting a specific vacancy, get the job ad (ask, or `fetch_url` it)
   and mirror its keywords in the summary and skills.
3. Structure sections in this order:
   - Header: name + title, phone, email, city, LinkedIn (in the title and
     first section body)
   - Ringkasan Profil: 3 lines, value-focused, **bold** the headline skills
   - Pengalaman Kerja: reverse-chronological; each role = `**Jabatan — 
     Perusahaan** (periode)` then `- ` achievement bullets with numbers
     ("meningkatkan X sebesar 30%"), not job-description prose
   - Pendidikan, Keahlian, Sertifikasi (bullets)
4. Keep it to 1–2 pages of content. Achievements over duties; numbers over
   adjectives; no photo unless asked (Indonesian employers sometimes want one
   — ask once and `remember` the answer).
5. `create_document` format `docx`. Offer a `pdf` twin for application portals.
6. Offer to save the master CV data so future tailoring is instant.
