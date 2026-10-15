// A full-length screenshot of a page, for checking the website's layout.
//   node packaging/media/page_shot.mjs http://127.0.0.1:48720/ out.png [width]
import { writeFileSync } from 'node:fs';
import { launch, sleep } from './cdp.mjs';

const [url, out, width = '1440'] = process.argv.slice(2);
const b = await launch({ width: +width, height: 900, port: 9466 });
try {
  await b.goto(url, 3000);
  // Show everything the scroll animations would reveal, and let the videos reach a frame
  await b.eval("document.querySelectorAll('.reveal').forEach(e => e.classList.add('in')); true");
  await sleep(2500);
  const height = await b.eval('document.documentElement.scrollHeight');
  writeFileSync(out, await b.shot('png', 90, { x: 0, y: 0, width: +width, height }));
  console.log(`${out}: ${width}x${height}`);
} finally {
  await b.close();
}
