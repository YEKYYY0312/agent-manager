const CSP_META_TAG = /<meta\s+http-equiv=["']Content-Security-Policy["'][^>]*>\s*/i;

export function stripCspForVite(html: string): string {
  return html.replace(CSP_META_TAG, '');
}
