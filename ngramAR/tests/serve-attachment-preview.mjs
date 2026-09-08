import { build } from 'esbuild';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, extname, sep } from 'node:path';
import { randomBytes } from 'node:crypto';
const ar=fileURLToPath(new URL('../',import.meta.url));
const output=resolve(ar,'../.runtime/attachment-preview'), dist=resolve(ar,'packages/surface-webxr/dist');
await mkdir(output,{recursive:true});
await build({entryPoints:[resolve(ar,'tests/attachment-preview.ts')],bundle:true,format:'esm',outfile:resolve(output,'attachment-preview.js'),logLevel:'info'});
if(process.argv.includes('--build-only'))process.exit(0);
const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.ttf':'font/ttf','.woff2':'font/woff2','.svg':'image/svg+xml','.png':'image/png'};
const files=new Map(); let fail=false, slow=false;
createServer(async(req,res)=>{try {
  const path=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
  if(path==='/fixture/fail') {fail=true;res.end('ok');return;}
  if(path==='/fixture/slow') {slow=true;res.end('ok');return;}
  if(path==='/api/shells/fixture/attachments' && req.method==='POST') {
    const parts=[];for await(const part of req)parts.push(part);
    if(fail){fail=false;res.writeHead(502,{'Content-Type':'application/json'}).end(JSON.stringify({error:'Fixture upload failed. Retry; your draft is still here.'}));return;}
    if(slow){slow=false;await new Promise(resolve=>setTimeout(resolve,8000));}
    if(res.destroyed)return;
    const body=Buffer.concat(parts),id=randomBytes(16).toString('hex');
    const item={id,name:decodeURIComponent(req.headers['x-ngram-filename']),mime:req.headers['content-type'],size:body.length};
    files.set(id,{...item,body});res.writeHead(201,{'Content-Type':'application/json'}).end(JSON.stringify(item));return;
  }
  const match=path.match(/^\/api\/shells\/fixture\/attachments\/([a-f0-9]{32})$/);
  if(match){const file=files.get(match[1]);if(!file){res.writeHead(404).end();return;}res.writeHead(200,{'Content-Type':file.mime}).end(file.body);return;}
  const file=path==='/attachment-preview.js'?resolve(output,'attachment-preview.js'):resolve(dist,'.'+(path==='/'?'/index.html':path));
  if(![output,dist].some(root=>file.startsWith(root+sep))){res.writeHead(403).end();return;}
  let data=await readFile(file);if(path==='/')data=data.toString().replace('src="app.js"','src="/attachment-preview.js"');
  res.writeHead(200,{'Content-Type':types[extname(file)]??'application/octet-stream','Cache-Control':'no-store'});res.end(data);
}catch {if(!res.headersSent)res.writeHead(404);res.end();}}).listen(4177,'127.0.0.1',()=>console.log('Attachment review: http://localhost:4177/'));
