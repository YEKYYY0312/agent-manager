import { stripCspForVite } from './devCsp.ts';

const html = '<head><meta http-equiv="Content-Security-Policy" content="style-src \'self\'" /><title>Agent DevTools</title></head>';
const transformed = stripCspForVite(html);

if (transformed.includes('Content-Security-Policy')) {
  throw new Error('Vite development HTML must not retain the static CSP meta tag');
}
if (!transformed.includes('<title>Agent DevTools</title>')) {
  throw new Error('Vite development CSP transform must preserve other head content');
}
