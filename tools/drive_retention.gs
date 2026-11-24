/**
 * Retention of the daily Cockpit backups in Google Drive (PC-62). Draft: the owner installs it by hand in Apps Script (script.google.com,
 * a new project under the owner's own account), runs installDailyTrigger() once and logs that in the manual-interventions log.
 *
 * In every sub-folder of the Drive folder `project_cockpits` (one per project's cockpit) it keeps the newest KEEP backup archives and moves
 * the older ones to the bin. It is a count, not an age: when the backups pause, nothing is removed, so the last ones are never lost.
 * Only archives named like the backup job's (<project>-YYYY-MM-DD[-n].tar.gz) are counted or touched; every other file is left alone.
 */
const ROOT_NAME = 'project_cockpits';
const KEEP = 30;
const ARCHIVE = /^.+-(\d{4}-\d{2}-\d{2})(?:-(\d+))?\.tar\.gz$/;

function keepNewestBackups() {
  const roots = DriveApp.getFoldersByName(ROOT_NAME);
  while (roots.hasNext()) {
    const subs = roots.next().getFolders();
    while (subs.hasNext()) pruneFolder_(subs.next());
  }
}

/** Newest first: by the date in the name, then by the suffix (a later run of the same day has a higher one), then by creation time. */
function pruneFolder_(folder) {
  const files = [];
  const it = folder.getFiles();
  while (it.hasNext()) {
    const f = it.next();
    const m = ARCHIVE.exec(f.getName());
    if (m) files.push({ file: f, day: m[1], n: Number(m[2] || 1), created: f.getDateCreated().getTime() });
  }
  files.sort((a, b) => b.day.localeCompare(a.day) || b.n - a.n || b.created - a.created);
  files.slice(KEEP).forEach(x => {
    console.log('%s: moving %s to the bin (%d archives, keeping %d)', folder.getName(), x.file.getName(), files.length, KEEP);
    x.file.setTrashed(true);
  });
}

/** Run once by hand: a daily trigger for keepNewestBackups (a second run would add a second trigger, so it removes earlier ones first). */
function installDailyTrigger() {
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'keepNewestBackups').forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('keepNewestBackups').timeBased().everyDays(1).atHour(6).create();
}
