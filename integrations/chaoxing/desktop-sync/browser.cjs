/* Every collector uses its own ephemeral browser context, never a personal profile. */
'use strict';
const fs = require('node:fs');

function browserOptions({headless=true, platform=process.platform, exists=fs.existsSync, chromiumPath}={}) {
  if (platform === 'win32') return {channel:'msedge', headless};
  if (platform === 'darwin') {
    for (const executablePath of [
      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    ]) if (exists(executablePath)) return {executablePath, headless};
  }
  if (chromiumPath && exists(chromiumPath)) return {executablePath:chromiumPath, headless};
  const error = new Error('Install Google Chrome or run npx playwright install chromium in integrations/chaoxing/desktop-sync.');
  error.code = 'CAMPUSINBOX_BROWSER_MISSING';
  throw error;
}
async function launchBrowser(chromium, headless=true) {
  return chromium.launch(browserOptions({headless, chromiumPath:chromium.executablePath()}));
}
module.exports={browserOptions,launchBrowser};
