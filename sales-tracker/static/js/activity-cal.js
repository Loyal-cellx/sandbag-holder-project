// GitHub-style contribution calendar, shared by the dashboard and analytics pages.
// Expects daily = {start: "YYYY-MM-DD", counts: [int, ...]} (one entry per day).
// Markup contract: root contains .cal-months, .cal-grid, and optionally [data-cal-summary].
function buildActivityCalendar(root, daily) {
  var grid = root.querySelector('.cal-grid');
  var months = root.querySelector('.cal-months');
  var summary = root.querySelector('[data-cal-summary]');
  if (!grid || !months) return;
  grid.textContent = '';
  months.textContent = '';

  var start = new Date(daily.start + 'T00:00:00');
  var counts = daily.counts;
  var MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  var pad = start.getDay();               // 0 = Sunday, GitHub's top row
  for (var p = 0; p < pad; p++) {
    var e = document.createElement('div');
    e.className = 'cal-cell empty';
    grid.appendChild(e);
  }

  var total = 0, activeDays = 0, lastLabelCol = -2;
  for (var i = 0; i < counts.length; i++) {
    var d = new Date(start.getTime());
    d.setDate(start.getDate() + i);
    var n = counts[i];
    var lvl = n > 3 ? 4 : n;
    var cell = document.createElement('div');
    cell.className = 'cal-cell' + (lvl ? ' l' + lvl : '');
    cell.title = d.toISOString().slice(0, 10) + (n ? ' — ' + n + ' sale' + (n > 1 ? 's' : '') : ' — no sales');
    grid.appendChild(cell);
    if (n) { total += n; activeDays++; }

    // month label above the first column that contains the 1st (or the start day)
    if (i === 0 || d.getDate() === 1) {
      var col = Math.floor((i + pad) / 7);
      if (col > lastLabelCol + 1) {      // skip a label that would crowd the previous one
        var m = document.createElement('span');
        m.textContent = MONTHS[d.getMonth()];
        m.style.gridColumnStart = col + 1;
        months.appendChild(m);
        lastLabelCol = col;
      }
    }
  }
  if (summary) {
    summary.textContent = total + ' sales across ' + activeDays + ' active days';
  }
}
