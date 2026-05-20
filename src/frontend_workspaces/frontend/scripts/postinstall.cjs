#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

// Fix express's path-to-regexp import for compatibility with v8.x
const layerFile = path.join(__dirname, '../node_modules/express/lib/router/layer.js');
console.log('Looking for:', layerFile);
if (fs.existsSync(layerFile)) {
  let code = fs.readFileSync(layerFile, 'utf8');
  if (code.includes("var pathRegexp = require('path-to-regexp');")) {
    code = code.replace(
      "var pathRegexp = require('path-to-regexp');",
      "var {pathToRegexp: pathRegexp} = require('path-to-regexp');"
    );
    fs.writeFileSync(layerFile, code);
    console.log('✓ Patched express path-to-regexp import');
  } else {
    console.log('Pattern not found in file');
  }
} else {
  console.log('File not found at:', layerFile);
  // Try parent directory
  const altLayerFile = path.join(__dirname, '../../node_modules/.pnpm/express@4.22.2/node_modules/express/lib/router/layer.js');
  console.log('Trying alternate path:', altLayerFile);
  if (fs.existsSync(altLayerFile)) {
    let code = fs.readFileSync(altLayerFile, 'utf8');
    if (code.includes("var pathRegexp = require('path-to-regexp');")) {
      code = code.replace(
        "var pathRegexp = require('path-to-regexp');",
        "var {pathToRegexp: pathRegexp} = require('path-to-regexp');"
      );
      fs.writeFileSync(altLayerFile, code);
      console.log('✓ Patched express path-to-regexp import (alternate path)');
    }
  }
}
