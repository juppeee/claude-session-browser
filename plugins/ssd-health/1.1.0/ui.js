// Drives - the reference plugin for the plugin API.
//
// Terminology follows Windows: a *disk* is the physical device, a *drive* is a
// volume with a letter. Each disk card carries the drives that sit on it, so
// with several disks it is clear what belongs where.
//
// The plugin gets a container and the csb object, nothing else: no api, no
// STATE. Data comes from backend.py through csb.call(), and the app's own
// styles are reused so the tab does not look bolted on.

const GB = 1024 * 1024 * 1024;
let DATEN = null;
let OFFEN = {};      // Plattennummer -> Detailbereich ausgeklappt?

function groesse(bytes) {
  if (!bytes) return '-';
  const tb = bytes / (1024 * GB);
  return tb >= 1 ? tb.toFixed(2) + ' TB' : Math.round(bytes / GB) + ' GB';
}

function punkt(zustand) {
  const z = String(zustand || '').toLowerCase();
  if (z === 'healthy') return 'ok';
  if (z === 'warning') return 'wait';
  return z ? 'err' : '';
}

function zustandHtml(zustand) {
  return '<span class="dot ' + punkt(zustand) + '"></span>' + (zustand || '?');
}

function balken(frei, gesamt) {
  if (!gesamt) return '';
  const belegt = Math.max(0, Math.min(100, Math.round((1 - frei / gesamt) * 100)));
  const farbe = belegt >= 90 ? 'var(--err)' : belegt >= 75 ? 'var(--warn)' : 'var(--accent)';
  return '<div class="dh-bar"><div style="width:' + belegt + '%;background:' + farbe + '"></div></div>'
       + '<div class="desc">' + csb.t('{pct}% used · {frei} free', { pct: belegt, frei: groesse(frei) })
       + '</div>';
}

function laufwerkHtml(v) {
  const name = v.letter + ':' + (v.label ? ' — ' + v.label : '');
  return '<div class="row2"><div><div class="lbl">' + name + '</div>'
       + '<div class="desc">' + csb.t('Health') + ': ' + zustandHtml(v.health)
       + (v.filesystem ? ' · ' + v.filesystem : '') + '</div>'
       + balken(v.free, v.size) + '</div><div>' + groesse(v.size) + '</div></div>';
}

function smartZeilen(s) {
  const zeilen = [];
  const dazu = (text, wert, einheit) => {
    if (wert === null || wert === undefined || wert === '') return;
    zeilen.push([csb.t(text), wert + (einheit || '')]);
  };
  dazu('Percentage used', s.wear, ' %');
  dazu('Temperature', s.temperature, ' °C');
  dazu('Highest temperature', s.temperature_max, ' °C');
  dazu('Power-on hours', s.hours, '');
  dazu('Power cycles', s.power_cycles, '');
  dazu('Read errors', s.read_errors, '');
  dazu('Read errors, uncorrected', s.read_errors_uncorrected, '');
  dazu('Write errors', s.write_errors, '');
  dazu('Write errors, uncorrected', s.write_errors_uncorrected, '');
  return zeilen;
}

function detailHtml(p) {
  const zeilen = smartZeilen(p.smart || {});
  if (!zeilen.length) {
    return '<div class="desc">'
      + csb.t('Windows only hands these out to an administrator. Everything else on this page needs no rights at all.')
      + '</div>';
  }
  return '<div class="dt-rows">'
    + zeilen.map(([k, v]) => '<div class="k">' + k + '</div><div class="v">' + v + '</div>').join('')
    + '</div>';
}

function platteHtml(p) {
  const meine = (DATEN.volumes || []).filter(v => v.disk === p.id);
  const kopf = [[csb.t('Health'), zustandHtml(p.health)],
                [csb.t('Capacity'), groesse(p.size)]];
  if (p.bus) kopf.push([csb.t('Bus'), p.bus]);
  if (p.firmware) kopf.push([csb.t('Firmware'), p.firmware]);
  const offen = !!OFFEN[p.id];
  return '<div class="card">'
    + '<h2>' + (p.name || '?') + '</h2>'
    + '<div class="sub">' + (p.media || csb.t('Unknown type'))
    + (p.serial ? ' · ' + csb.t('Serial number') + ' ' + p.serial : '') + '</div>'
    + '<div class="dt-rows">'
    + kopf.map(([k, v]) => '<div class="k">' + k + '</div><div class="v">' + v + '</div>').join('')
    + '</div>'
    + (meine.length
        ? '<div class="dh-sect">' + csb.t('Drives on this disk') + '</div>' + meine.map(laufwerkHtml).join('')
        : '<div class="desc">' + csb.t('No drive letter on this disk.') + '</div>')
    + '<div class="field"><button class="btn" data-detail="' + p.id + '">'
    + (offen ? csb.t('Hide details') : csb.t('Details')) + '</button></div>'
    + (offen ? detailHtml(p) : '')
    + '</div>';
}

function zeichnen() {
  const platten = (DATEN.disks || []).map(platteHtml).join('');
  const ohne = (DATEN.volumes || []).filter(v => !v.disk);
  const rest = ohne.length
    ? '<div class="secthead">' + csb.t('Drives without a disk') + '</div>'
      + '<div class="card">' + ohne.map(laufwerkHtml).join('') + '</div>'
    : '';
  const quelle = '<div class="card"><div class="sub">'
    + csb.t('Health comes from Windows ({quelle}), not from the drive manufacturer.',
            { quelle: DATEN.source || '?' })
    + (DATEN.smart_available ? ''
        : ' ' + csb.t('Wear, power-on hours and temperature need administrator rights.'))
    + '</div></div>';
  csb.el.innerHTML = '<div class="secthead">' + csb.t('Disks') + '</div>' + platten + rest + quelle;
  csb.el.querySelectorAll('button[data-detail]').forEach(b => {
    b.onclick = () => { OFFEN[b.dataset.detail] = !OFFEN[b.dataset.detail]; zeichnen(); };
  });
  const n = (DATEN.disks || []).length;
  csb.setStatus(csb.t('{n} disks', { n: n }));
}

// Eigene Klassen statt Inline-Stil - dieselbe Regel wie in der App.
const STIL = document.createElement('style');
STIL.textContent =
  '.dh-bar{height:7px;border-radius:5px;background:var(--border);overflow:hidden;margin-top:7px}'
+ '.dh-bar>div{height:100%}'
+ '.dh-sect{margin:14px 0 2px;font-size:11.5px;font-weight:700;letter-spacing:.6px;'
+ 'text-transform:uppercase;color:var(--muted)}';
document.head.appendChild(STIL);

csb.onEnter(async () => {
  if (!csb.el.innerHTML) {
    csb.el.innerHTML = '<div class="card"><div class="sub">' + csb.t('Loading…') + '</div></div>';
  }
  const antwort = await csb.call('drives', {});
  if (!antwort || !antwort.ok) {
    csb.el.innerHTML = '<div class="card"><div class="sub">'
      + csb.t('Could not read the disks: {grund}', { grund: (antwort && antwort.error) || '?' })
      + '</div></div>';
    return;
  }
  DATEN = antwort.result || {};
  zeichnen();
});
