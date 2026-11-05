// Bundles the module (main.js, lib.js and @companion-module/base) into one main.js, the way the modules Companion
// installs are made. Run by tools/build_companion.py:  node build.js <out/main.js> <out/meta.json>
'use strict'

const fs = require('node:fs')
const esbuild = require('esbuild')

const [outfile, metafile] = process.argv.slice(2)
const result = esbuild.buildSync({
	entryPoints: ['main.js'],
	bundle: true,
	platform: 'node',
	target: 'node22',
	format: 'cjs',
	outfile,
	metafile: true,
	legalComments: 'none', // (tools/build_companion.py puts every package's licence in main.js.LICENSE.txt)
	logLevel: 'warning',
})
fs.writeFileSync(metafile, JSON.stringify(result.metafile))
