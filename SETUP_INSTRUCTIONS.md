# OmegaTV Setup Guide for New Mac

## 1. Connect the Drive
Plug in your **External SSD** (`/Volumes/Extreme SSD`).
*   This drive now holds all your media (`1_INBOX`, `2_VAULT`, etc.) and your secret key.

## 2. Get the Code
Open Terminal on the new Mac and run:
```bash
git clone https://github.com/haukur1982/SubtitleWorkflow.git
cd SubtitleWorkflow
```

## 3. Install Dependencies
You'll need to install the Python libraries.
```bash
pip3 install -r requirements.txt
```

## 4. Restore the Secret Key
Copy the key from the secure folder on your drive to the project folder:
```bash
cp "/Volumes/Extreme SSD/Omega_Work/_SECRETS_DO_NOT_SHARE/service_account.json" .
```

## 5. Link the Media Folders
Connect the code to the media on the drive (run this inside `SubtitleWorkflow`):
```bash
# Remove empty default folders if they exist
rm -rf 1_INBOX 2_VAULT 3_EDITOR 3_TRANSLATED_DONE 4_DELIVERY 99_ERRORS

# Create links to the SSD
ln -s "/Volumes/Extreme SSD/Omega_Work/1_INBOX" ./1_INBOX
ln -s "/Volumes/Extreme SSD/Omega_Work/2_VAULT" ./2_VAULT
ln -s "/Volumes/Extreme SSD/Omega_Work/3_EDITOR" ./3_EDITOR
ln -s "/Volumes/Extreme SSD/Omega_Work/3_TRANSLATED_DONE" ./3_TRANSLATED_DONE
ln -s "/Volumes/Extreme SSD/Omega_Work/4_DELIVERY" ./4_DELIVERY
ln -s "/Volumes/Extreme SSD/Omega_Work/99_ERRORS" ./99_ERRORS
```

## 6. Start the System
```bash
sh start_omega.sh
```

## Troubleshooting
- **Dashboard not loading?** Check `http://127.0.0.1:8080`.
- **Permission errors?** Make sure Terminal has "Full Disk Access" in System Settings if it complains about accessing the external drive.
- **Remote access (optional):** By default the dashboard binds to `127.0.0.1` (local only). To expose it, start with `OMEGA_DASH_HOST=0.0.0.0` and set `OMEGA_ADMIN_TOKEN` for control actions.
