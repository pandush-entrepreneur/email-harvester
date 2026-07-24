# CLEANUP — do this first

The repo currently contains client personal data in public git history. Deleting the files is not enough; git keeps every prior commit. History must be rewritten.

## 1. Make the repo private, immediately

GitHub → Settings → General → Danger Zone → Change visibility → Private.

Do this before anything else. It stops further exposure while you clean up.

## 2. Back up the data outside git

```bash
mkdir -p ~/harvester-data
cp -r email_engine_output ~/harvester-data/
cp -r website_harvester_output ~/harvester-data/
cp *.csv ~/harvester-data/
```

These are your deliverables. Keep them, just not in git.

## 3. Purge them from history

```bash
pip install git-filter-repo

git filter-repo --force \
  --path email_engine_output --path website_harvester_output \
  --path-glob '*.csv' --path-glob '*.jsonl' \
  --invert-paths

git push origin --force --all
git push origin --force --tags
```

Then confirm nothing remains:

```bash
git log --all --name-only --pretty=format: | sort -u | grep -E '\.csv|\.jsonl' || echo "clean"
```

## 4. Install the real .gitignore

Use the `.gitignore` in this bundle before re-adding anything.

## 5. Rotate the MailTester key

Not because it leaked here, it did not. Because it was pasted inline on the command line, so it is sitting in PowerShell history on disk.

```powershell
Clear-History
Remove-Item (Get-PSReadlineOption).HistorySavePath
```

Then regenerate the key in your MailTester account.

## 6. If the repo was public for a while

Anyone could have cloned it, and GitHub caches forks and views. Assume the data is out. For UK and EU contacts specifically, consider whether this needs recording as a personal data breach under UK GDPR Article 33. Speak to a lawyer rather than taking my word for it.

## Going forward

Client data never enters this repo. The engine is the asset. The lists belong to clients and live on the server, in the gitignored `clients/` folder, deleted when the engagement ends.
