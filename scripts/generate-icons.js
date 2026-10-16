import sharp from 'sharp';
import fs from 'fs';
import path from 'path';

const publicDir = path.resolve('public');
if (!fs.existsSync(publicDir)) {
  fs.mkdirSync(publicDir, { recursive: true });
}

const svgPath = path.resolve(publicDir, 'icon.svg');
const svgBuffer = fs.readFileSync(svgPath);

async function generate() {
  console.log('Generating PNG icons from SVG...');
  
  // 192x192
  await sharp(svgBuffer)
    .resize(192, 192)
    .png()
    .toFile(path.resolve(publicDir, 'pwa-192x192.png'));

  // 512x512
  await sharp(svgBuffer)
    .resize(512, 512)
    .png()
    .toFile(path.resolve(publicDir, 'pwa-512x512.png'));

  // 180x180 apple touch icon
  await sharp(svgBuffer)
    .resize(180, 180)
    .png()
    .toFile(path.resolve(publicDir, 'apple-touch-icon.png'));

  // Maskable 512x512 with 15% safe padding
  const innerSize = Math.round(512 * 0.72); // ~368px
  const innerBuffer = await sharp(svgBuffer).resize(innerSize, innerSize).png().toBuffer();

  await sharp({
    create: {
      width: 512,
      height: 512,
      channels: 4,
      background: { r: 9, g: 13, b: 22, alpha: 1 }
    }
  })
  .composite([{ input: innerBuffer, gravity: 'center' }])
  .png()
  .toFile(path.resolve(publicDir, 'pwa-maskable-512x512.png'));

  console.log('All PWA icons generated successfully.');
}

generate().catch(err => {
  console.error(err);
  process.exit(1);
});
