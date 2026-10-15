// A small Chrome DevTools Protocol client: headless Chrome with a throwaway profile, no packages needed
// (Node 22+ has fetch and WebSocket built in). Used to take the dashboard's screenshots and video.
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findChrome() {
  const found = [process.env.CHROME_PATH, 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find((p) => p && existsSync(p));
  if (!found) throw new Error('Chrome not found: set CHROME_PATH');
  return found;
}

export async function launch({ port = 9455, width = 1440, height = 900, scale = 1 } = {}) {
  const profile = mkdtempSync(join(tmpdir(), 'vocalm-media-'));
  const proc = spawn(findChrome(), ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--mute-audio', '--disable-extensions', '--hide-scrollbars',
    '--force-color-profile=srgb', `--window-size=${width},${height}`, 'about:blank'], { stdio: 'ignore' });
  let targets = [];
  for (let i = 0; i < 150 && !targets.some((t) => t.type === 'page'); i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); } catch { await sleep(200); }
  }
  const page = targets.find((t) => t.type === 'page');
  if (!page) throw new Error("Chrome didn't start");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map();
  const listeners = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? rej(new Error(m.error.message)) : res(m.result);
    } else if (m.method) {
      for (const l of listeners) l(m);
      if (m.method === 'Runtime.exceptionThrown') console.error('page error:', m.params.exceptionDetails.text);
    }
  };
  const send = (method, params = {}) => new Promise((res, rej) => {
    const msgId = ++id;
    pending.set(msgId, { res, rej });
    ws.send(JSON.stringify({ id: msgId, method, params }));
  });
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: scale, mobile: false });
  await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'no-preference' }] });

  const browser = {
    send,
    on: (fn) => listeners.push(fn),
    async goto(url, settle = 1500) {
      await send('Page.navigate', { url });
      await sleep(settle);
    },
    async eval(expression) {
      const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' in: ' + expression.slice(0, 80));
      return r.result.value;
    },
    async waitFor(expression, timeout = 15000) {
      const end = Date.now() + timeout;
      while (Date.now() < end) {
        if (await browser.eval(expression)) return;
        await sleep(100);
      }
      throw new Error('timed out waiting for: ' + expression);
    },
    async shot(format = 'png', quality = 90, clip) {
      const params = { format, captureBeyondViewport: !!clip };
      if (format === 'jpeg') params.quality = quality;
      if (clip) params.clip = { ...clip, scale: 1 };
      return Buffer.from((await send('Page.captureScreenshot', params)).data, 'base64');
    },
    async close() {
      try { await send('Browser.close'); } catch { /* already gone */ }
      proc.kill();
      await sleep(400);
      try { rmSync(profile, { recursive: true, force: true }); } catch { /* Chrome may still hold it briefly */ }
    },
  };
  return browser;
}
