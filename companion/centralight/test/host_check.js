// A stand-in for Companion's module host, run by CentraLight's tests (tests/test_live_m16.py): it starts the BUILT
// module (pkg/main.js from centralight-<version>.tgz, unpacked) the way Companion does, with an IPC channel, registers
// it, starts it with a connection to the CentraLight remote on the port given, and prints what the module tells it, as
// lines of JSON: its status, its actions, variables and presets, a key's feedback, and the answer to a pressed key.
//     node host_check.js <the unpacked pkg folder> <port>
'use strict'

const { fork } = require('node:child_process')
const path = require('node:path')

const [folder, port] = process.argv.slice(2)
const out = (data) => process.stdout.write(JSON.stringify(data) + '\n')
if (!(Number(port) > 0) || Number(port) === 8746) {
	out({ step: 'error', message: 'give the port of a CentraLight started for the test (never 8746, the real one)' })
	process.exit(1)
}
const seen = { status: [], actions: null, variables: {}, presets: null, feedback: {}, logs: [] }
const waiting = new Map()
let nextId = 1

const child = fork(path.join(folder, 'main.js'), [], {
	cwd: folder,
	env: { ...process.env, MODULE_MANIFEST: 'companion/manifest.json', CONNECTION_ID: 'check', VERIFICATION_TOKEN: 'token' },
	stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
})
child.stdout.on('data', () => {})
child.stderr.on('data', (data) => seen.logs.push(String(data).trim()))

function call(name, payload) {
	const id = nextId++
	child.send({ direction: 'call', name, payload: JSON.stringify(payload), callbackId: id })
	return new Promise((resolve, reject) => waiting.set(id, { resolve, reject }))
}

child.on('message', (message) => {
	if (message.direction === 'response') {
		const pending = waiting.get(message.callbackId)
		waiting.delete(message.callbackId)
		if (!pending) return
		const data = message.payload ? JSON.parse(message.payload) : undefined
		if (message.success) pending.resolve(data)
		else pending.reject(new Error((data && data.message) || String(message.payload)))
		return
	}
	const data = message.payload ? JSON.parse(message.payload) : undefined
	if (message.name === 'set-status') seen.status.push(data.status)
	else if (message.name === 'setActionDefinitions') seen.actions = data.actions
	else if (message.name === 'setPresetDefinitions') seen.presets = data.presets
	else if (message.name === 'setVariableValues') for (const v of data.newValues) seen.variables[v.id] = v.value
	else if (message.name === 'setVariableDefinitions') for (const v of data.newValues || []) seen.variables[v.id] = v.value
	else if (message.name === 'updateFeedbackValues') for (const v of data.values) seen.feedback[v.id] = v.value
	else if (message.name === 'log-message') seen.logs.push(`${data.level}: ${data.message}`)
	if (message.callbackId) {
		child.send({ direction: 'response', callbackId: message.callbackId, success: true, payload: JSON.stringify({}) })
	}
	if (message.name === 'register') start().catch((error) => finish({ step: 'error', message: error.message }))
})

function until(check, what, seconds = 8) {
	return new Promise((resolve, reject) => {
		const started = Date.now()
		const timer = setInterval(() => {
			if (check()) {
				clearInterval(timer)
				resolve()
			} else if (Date.now() - started > seconds * 1000) {
				clearInterval(timer)
				reject(new Error(`no ${what} (statuses: ${seen.status.join(', ')}; logs: ${seen.logs.slice(-6).join(' | ')})`))
			}
		}, 20)
	})
}

async function start() {
	out({ step: 'registered' })
	const key = { id: 'fb1', controlId: 'bank:1', feedbackId: 'on_base', options: { input: 'warm-white' }, upgradeIndex: null,
		disabled: false, isInverted: false }
	const started = await call('init', {
		// (isFirstInit false: on a first init Companion's library ignores the config given and uses the defaults, which
		// would be the real remote's port)
		label: 'centralight', isFirstInit: false, config: { host: '127.0.0.1', port: Number(port), key: '' },
		lastUpgradeIndex: -1, feedbacks: { fb1: key }, actions: {},
	})
	out({ step: 'init', newUpgradeIndex: started.newUpgradeIndex })
	await until(() => seen.status.includes('ok'), 'connection')
	await until(() => (seen.actions || []).some((a) => a.id === 'base' && a.options[0].choices.length > 0), 'inputs in the actions')
	const base = seen.actions.find((a) => a.id === 'base')
	out({ step: 'actions', count: seen.actions.length, inputs: base.options[0].choices.map((c) => c.id) })
	await until(() => seen.feedback.fb1 === true, 'the key lit for the input on Base')
	out({ step: 'feedback', value: seen.feedback.fb1 })
	await until(() => typeof seen.variables.master === 'number', 'the variables')
	out({ step: 'variables', show: seen.variables.show, master: seen.variables.master, base: seen.variables.base })
	await until(() => Array.isArray(seen.presets) && seen.presets.length > 0, 'the presets')
	out({ step: 'presets', count: seen.presets.length })
	await call('executeAction', {
		action: { id: 'a1', controlId: 'bank:2', actionId: 'base', options: { input: 'worship-blue', how: 'cut', duration: 1 },
			upgradeIndex: null, disabled: false },
		surfaceId: undefined,
	})
	out({ step: 'pressed' })
	const config = await call('getConfigFields', {})
	out({ step: 'config', fields: config.fields.map((field) => field.id) })
	await call('destroy', {})
	finish({ step: 'done', warnings: seen.logs.filter((line) => /^(warn|error)/.test(line)) })
}

function finish(last) {
	out(last)
	child.kill()
	process.exit(last.step === 'done' ? 0 : 1)
}

child.on('exit', (code) => finish({ step: 'error', message: `the module stopped (${code})` }))
setTimeout(() => finish({ step: 'error', message: 'timed out' }), 30000)
