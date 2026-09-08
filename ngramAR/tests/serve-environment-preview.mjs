import { build } from 'esbuild';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, sep, extname } from 'node:path';
import { deflateSync } from 'node:zlib';
const ar = fileURLToPath(new URL('../', import.meta.url));
const output = resolve(ar, '../.runtime/environment-preview'), dist = resolve(ar, 'packages/surface-webxr/dist');
await mkdir(output, {recursive:true});
await build({entryPoints:[resolve(ar,'tests/environment-preview.ts')],bundle:true,format:'esm',outfile:resolve(output,'environment-preview.js'),logLevel:'info'});
if (process.argv.includes('--build-only')) process.exit(0);
function crc(bytes) { let n = 0xffffffff; for (const b of bytes) { n ^= b; for (let i=0;i<8;i++) n = (n>>>1) ^ ((n&1) ? 0xedb88320 : 0); } return (n ^ 0xffffffff) >>> 0; }
function chunk(type, data) { const bytes = Buffer.concat([Buffer.from(type),data]), length = Buffer.alloc(4), check = Buffer.alloc(4); length.writeUInt32BE(data.length); check.writeUInt32BE(crc(bytes)); return Buffer.concat([length,bytes,check]); }
const width=512,height=256, raw=Buffer.alloc(height*(1+width*3)), rgbe=Buffer.alloc(width*height*4);
for(let y=0;y<height;y++) for(let x=0;x<width;x++) {
  const sky=y<height/2, bright=x>width*.3&&x<width*.36&&y<height*.45;
  const rgb=bright?[255,210,120]:sky?[50,100,220]:[25,40,55];
  rgb.forEach((v,c)=>{raw[y*(1+width*3)+1+x*3+c]=v;rgbe[(y*width+x)*4+c]=v;});
  rgbe[(y*width+x)*4+3]=128;
}
const header=Buffer.alloc(13);header.writeUInt32BE(width);header.writeUInt32BE(height,4);header[8]=8;header[9]=2;
const png=Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',header),chunk('IDAT',deflateSync(raw)),chunk('IEND',Buffer.alloc(0))]);
const hdr=Buffer.concat([Buffer.from(`#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y ${height} +X ${width}\n`),rgbe]);
const types={'.js':'text/javascript','.css':'text/css','.ttf':'font/ttf','.woff2':'font/woff2','.svg':'image/svg+xml'};
createServer(async(req,res)=>{
  try {
    const path=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
    if(path==='/test-sky.png'||path==='/test-sky.hdr'){res.writeHead(200,{'Content-Type':path.endsWith('png')?'image/png':'image/vnd.radiance'});res.end(path.endsWith('png')?png:hdr);return;}
    const file=path==='/environment-preview.js'?resolve(output,'environment-preview.js'):resolve(dist,'.'+(path==='/'?'/index.html':path));
    if(![output,dist].some(root=>file.startsWith(root+sep))){res.writeHead(403).end();return;}
    let content=await readFile(file);
    if(path==='/')content=content.toString().replace('src="app.js"','src="/environment-preview.js"');
    res.writeHead(200,{'Content-Type':path==='/'?'text/html':types[extname(file)]??'application/octet-stream','Cache-Control':'no-store'});res.end(content);
  }catch{res.writeHead(404).end();}
}).listen(4177,'127.0.0.1',()=>console.log('Sky review http://127.0.0.1:4177/'));
