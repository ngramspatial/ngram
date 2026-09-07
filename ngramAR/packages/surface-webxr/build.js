import * as esbuild from 'esbuild';
import { cpSync, mkdirSync, readFileSync, writeFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const isWatch = process.argv.includes('--watch');
const isProd = !isWatch;

const outdir = resolve(__dirname, 'dist');
mkdirSync(outdir, { recursive: true });

cpSync(resolve(__dirname, 'public'), outdir, { recursive: true });

// Sandboxed app frames have opaque origins. Embed the font in their stylesheet
// so they need no cross-origin font request and retain the existing sandbox.
const fontData = readFileSync(resolve(__dirname, 'public/fonts/AzeretMono-Variable.ttf')).toString('base64');
const fontCSS = readFileSync(resolve(__dirname, 'public/fonts/azeret-mono.css'), 'utf8');
writeFileSync(resolve(outdir, 'fonts/azeret-mono-embedded.css'),
  fontCSS.replace('./AzeretMono-Variable.ttf', `data:font/ttf;base64,${fontData}`));

/** @type {import('esbuild').BuildOptions} */
const buildOptions = {
  entryPoints: [resolve(__dirname, 'src/main.ts')],
  outfile: resolve(outdir, 'app.js'),
  bundle: true,
  format: 'esm',
  platform: 'browser',
  target: 'es2022',
  minify: isProd,
  sourcemap: !isProd,
  logLevel: 'info',
};

if (isWatch) {
  const ctx = await esbuild.context(buildOptions);
  await ctx.watch();
  console.log('[surface-webxr] watching for changes...');
} else {
  await esbuild.build(buildOptions);
  console.log('[surface-webxr] build complete');
}
