// Screenshots and a video of the dashboard, taken from a Vocalm running in demo mode.
//   node packaging/media/dashboard.mjs http://127.0.0.1:48690 <out folder>
// Writes live.png, insights.png, heatmap.png, settings.png, download.png and frames/ (the video, as JPEGs plus
// frames.json with each frame's time), which make_media.py turns into the README's and the site's files.
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { launch, sleep } from './cdp.mjs';

const [base, out] = process.argv.slice(2);
mkdirSync(join(out, 'frames'), { recursive: true });
const save = (name, buf) => { writeFileSync(join(out, name), buf); console.log('  ' + name); };
const zone = "document.getElementById('status').dataset.zone";

const b = await launch({ width: 1440, height: 900 });
try {
  await b.goto(base + '/#/live', 2500);
  await b.eval("localStorage.setItem('vocalm.theme','dark'); localStorage.setItem('vocalm.days','30'); true");
  await b.goto(base + '/#/live', 2500);

  // The Live page mid-shout: wait for the scripted voice to go red, then let the meter and trace catch up
  await b.waitFor(`${zone} === 'calm'`, 40000);
  await b.waitFor(`${zone} === 'loud'`, 40000);
  await sleep(1500);
  save('live.png', await b.shot());

  await b.goto(base + '/#/insights', 2500);
  save('insights.png', await b.shot());
  await b.eval("document.getElementById('chartHeat').closest('.card').scrollIntoView({block:'start'}); window.scrollBy(0,-90); true");
  await sleep(700);
  save('heatmap.png', await b.shot());

  await b.goto(base + '/#/settings', 2000);
  await b.eval('window.scrollTo(0, 0); true');  // a hash change keeps the scroll position of the page before
  await sleep(600);
  save('settings.png', await b.shot());
  // Calibration and your line, cropped tight so they stay readable when shown small
  const box = await b.eval(`(() => {
    const a = document.getElementById('calibrate').getBoundingClientRect();
    const c = document.querySelector('.settings-grid .card').getBoundingClientRect();
    return {x: a.left - 20, y: a.top + scrollY - 20, width: a.width + 40, height: c.bottom - a.top + 40};
  })()`);
  save('calibrate.png', await b.shot('png', 90, box));
  await b.eval("document.getElementById('sBarLabel').closest('.card').scrollIntoView({block:'center'}); true");
  await sleep(600);
  save('settings-bar.png', await b.shot());

  // The video: one full loop of the scripted voice on the Live page, then a slow scroll through Insights
  await b.goto(base + '/#/live', 2000);
  await b.eval('window.scrollTo(0,0); true');
  await b.waitFor(`${zone} === 'quiet'`, 40000);
  await b.waitFor(`${zone} === 'calm'`, 40000);
  const frames = [];
  const grab = async () => {
    const t = Date.now();
    const name = `f${String(frames.length).padStart(4, '0')}.jpg`;
    writeFileSync(join(out, 'frames', name), await b.shot('jpeg', 88));
    frames.push({ name, t });
  };
  const until = async (ms, step) => { const end = Date.now() + ms; while (Date.now() < end) { await grab(); if (step) await step(); await sleep(15); } };
  await until(24500);
  await b.eval("location.hash = '#/insights'; true");
  await until(2200);
  const height = await b.eval('document.documentElement.scrollHeight - innerHeight');
  const scrollStart = Date.now();
  await until(9000, () => b.eval(`window.scrollTo(0, ${height} * Math.min(1, (Date.now() - ${scrollStart}) / 8000) ** 1.2); true`));
  writeFileSync(join(out, 'frames.json'), JSON.stringify(frames));
  console.log(`  frames/ (${frames.length} frames)`);

  // The download step, from the public release page (not signed in, so nothing personal)
  try {
    await b.goto('https://github.com/Orio-03/Vocalm/releases/latest', 3000);
    await b.waitFor("[...document.querySelectorAll('a')].some(a => /windows\\.zip$/.test(a.textContent.trim()))", 20000);
    const box = await b.eval(`(() => {
      const a = [...document.querySelectorAll('a')].find(a => /windows\\.zip$/.test(a.textContent.trim()));
      const block = a.closest('details') || a.closest('.Box') || a.parentElement.parentElement;
      block.scrollIntoView({block: 'center'});
      a.style.outline = '3px solid #3fbfad'; a.style.outlineOffset = '3px'; a.style.borderRadius = '4px';
      const r = block.getBoundingClientRect();
      return {x: Math.max(0, r.left - 16), y: Math.max(0, r.top - 16 + scrollY), width: Math.min(r.width + 32, 1200), height: r.height + 32};
    })()`);
    await sleep(600);
    save('download.png', await b.shot('png', 90, box));
  } catch (e) {
    console.log('  (skipped download.png: ' + e.message + ')');
  }
} finally {
  await b.close();
}
