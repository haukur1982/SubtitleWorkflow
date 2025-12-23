Remote Review Portal - Implementation Notes

Status
- Implemented locally and ready to deploy.
- Awaiting Cloud Run deployment + Gmail App Password + portal URL.

What was added
- Remote review inbox paths:
  - 1_INBOX/03_REMOTE_REVIEW/Classic
  - 1_INBOX/03_REMOTE_REVIEW/Modern_Look
  - 1_INBOX/03_REMOTE_REVIEW/Apple_TV
- Review artifacts in GCS:
  - review.json (source + translation + timecodes)
  - review_token.json (secure token + expiry)
  - review_corrections.json (reviewer edits)
- Email sender utility:
  - email_utils.py (SMTP over Gmail with TLS)
- Manager workflow (omega_manager.py):
  - Packages review.json after cloud approval
  - Emails reviewer link
  - Pauses at "Waiting for Remote Review"
  - Applies corrections when review_corrections.json arrives
- Review portal (Cloud Run ready):
  - review_portal.py (Flask)
  - templates/review_portal.html (simple edit UI)
  - cloud/review/Dockerfile + cloud/review/requirements.txt

Reviewer UI
- Shows: source English, translation (editable), timecodes, segment ID.
- Optional comment per segment + general note.
- No timing edits or audio to keep it safe and fast.

Config / env
- start_omega.sh loads .omega_secrets if present.
- Added env defaults:
  - OMEGA_REVIEW_PORTAL_URL
  - OMEGA_REVIEWER_EMAIL (defaults to hawk1982@me.com)
  - OMEGA_SMTP_USER (defaults to haukur1982@gmail.com)
  - OMEGA_SMTP_PASS (must be set via .omega_secrets)
  - OMEGA_SMTP_FROM
- .omega_secrets is gitignored; example in .omega_secrets.example.

Next steps to finish
1) Create Gmail App Password and put it in .omega_secrets
2) Deploy Cloud Run review portal
3) Set OMEGA_REVIEW_PORTAL_URL to the Cloud Run URL
4) Restart manager so email + remote review flow are active

Notes
- Remote review only shares text (no audio/video leaves local machine).
- Burn is blocked until remote reviewer submits corrections (as requested).
