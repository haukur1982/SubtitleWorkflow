import json
from google.cloud import storage

client = storage.Client()
bucket = client.bucket('omega-jobs-subtitle-project')
job_id = 'timessquarechurch_20251214-20251220T220720Z'

try:
    sk_content = bucket.blob(f'jobs/{job_id}/skeleton.json').download_as_bytes()
    skeleton = json.loads(sk_content)
    
    dr_content = bucket.blob(f'jobs/{job_id}/translation_draft.json').download_as_bytes()
    draft_data = json.loads(dr_content)
    draft = draft_data.get('translated_data', draft_data)
    
    ap_content = bucket.blob(f'jobs/{job_id}/approved.json').download_as_bytes()
    approved_data = json.loads(ap_content)
    approved = approved_data.get('segments', approved_data)

    print(f"Skeleton: {len(skeleton)}, Draft: {len(draft)}, Approved: {len(approved)}")

    with open('fine_tune_audit.txt', 'w', encoding='utf-8') as f:
        # Scan everything for hallow or bath
        for i in range(len(skeleton)):
            s_text = str(skeleton[i].get('text',''))
            low = s_text.lower()
            if 'hallow' in low or 'bath' in low or skeleton[i].get('id') == 655:
                f.write(f"ID: {skeleton[i].get('id')}\n")
                f.write(f"SRC: {s_text}\n")
                f.write(f"DRT: {draft[i].get('text')}\n")
                f.write(f"FIN: {approved[i].get('text')}\n\n")

except Exception as e:
    print(f"ERROR: {e}")
