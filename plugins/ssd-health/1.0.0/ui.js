// Drive health - the reference plugin for the plugin API.
//
// It gets a container and the csb object, nothing else: no api, no STATE. All
// data comes from backend.py through csb.call(), and the app's own styles
// (card, row2, lbl, desc, sub) are reused so the tab does not look bolted on.

const GB = 1024 * 1024 * 1024;

function groesse(bytes) {
  if (!bytes) return '-';
  const tb = bytes / (1024 * GB);
  return tb >= 1 ? tb.toFixed(2) + ' TB' : Math.round(bytes / GB) + ' GB';
}

function zustandPunkt(zustand) {
  const z = String(zustand || '').toLowerCase();
  if (z === 'healthy') return 'ok';
  if (z === 'warning') return 'wait';
  return z ? 'err' : '';
}

function balken(frei, gesamt) {
  if (!gesamt) return '';
  const belegt = Math.max(0, Math.min(100, Math.round((1 - frei / gesamt) * 100)));
  const farbe = belegt >= 90 ? 'var(--err)' : belegt >= 75 ? 'var(--warn)' : 'var(--accent)';
  return '<div style="height:7px;border-radius:5px;background:var(--border);overflow:hidden;margin-top:7px">'
       + '<div style="height:100%;width:' + belegt + '%;background:' + farbe + '"></div></div>'
       + '<div class="desc" style="margin-top:5px">' + belegt + '% '
       + csb.t('belegt') + ' · ' + groesse(frei) + ' ' + csb.t('frei') + '</div>';
}

function zeichnen(d) {
  const platten = (d.disks || []).map(p => {
    const zeilen = [];
    zeilen.push([csb.t('Zustand'), '<span class="dot ' + zustandPunkt(p.health) + '"></span>' + (p.health || '?')]);
    zeilen.push([csb.t('Größe'), groesse(p.size)]);
    if (p.bus) zeilen.push([csb.t('Anschluss'), p.bus]);
    if (p.wear !== null && p.wear !== undefined) zeilen.push([csb.t('Verschleiß'), p.wear + ' %']);
    if (p.hours) zeilen.push([csb.t('Betriebsstunden'), p.hours]);
    if (p.temperature) zeilen.push([csb.t('Temperatur'), p.temperature + ' °C']);
    return '<div class="card"><h2>' + (p.name || '?') + '</h2>'
         + '<div class="sub">' + (p.media || csb.t('Unbekannter Typ')) + '</div>'
         + '<div class="dt-rows">'
         + zeilen.map(([k, v]) => '<div class="k">' + k + '</div><div class="v">' + v + '</div>').join('')
         + '</div></div>';
  }).join('');

  const volumes = (d.volumes || []).map(v =>
    '<div class="row2"><div><div class="lbl">' + v.letter + ':'
    + (v.label ? ' — ' + v.label : '') + '</div>'
    + '<div class="desc">' + csb.t('Zustand') + ': '
    + '<span class="dot ' + zustandPunkt(v.health) + '"></span>' + (v.health || '?') + '</div>'
    + balken(v.free, v.size) + '</div><div>' + groesse(v.size) + '</div></div>').join('');

  const hinweis = d.wear_available ? '' :
    '<div class="card"><div class="sub">'
    + csb.t('Verschleiß, Betriebsstunden und Temperatur gibt Windows nur mit Administratorrechten heraus. Alles andere hier braucht keine.')
    + '</div></div>';

  csb.el.innerHTML = '<div class="settings">'
    + '<div class="secthead">' + csb.t('Laufwerke') + '</div>' + platten
    + '<div class="secthead">' + csb.t('Datenträger') + '</div>'
    + '<div class="card">' + (volumes || '<div class="sub">' + csb.t('Nichts gefunden.') + '</div>') + '</div>'
    + hinweis + '</div>';
}

csb.onEnter(async () => {
  if (!csb.el.innerHTML) {
    csb.el.innerHTML = '<div class="settings"><div class="card"><div class="sub">'
                     + csb.t('Lädt…') + '</div></div></div>';
  }
  const antwort = await csb.call('drives', {});
  if (!antwort || !antwort.ok) {
    csb.el.innerHTML = '<div class="settings"><div class="card"><div class="sub">'
      + csb.t('Laufwerke konnten nicht gelesen werden: {grund}',
              { grund: (antwort && antwort.error) || '?' }) + '</div></div></div>';
    return;
  }
  zeichnen(antwort.result || {});
});
