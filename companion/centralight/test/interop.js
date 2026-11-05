// Run by CentraLight's tests (tests/test_live_m16.py) against a real CentraLight remote on a test port: Node's own
// WebSocket (the one Companion's Node 22 has) opens the live connection, presses every action with its default
// options, and prints each answer as a line of JSON.
//     node interop.js <port> [key]
'use strict'

const lib = require('../lib')

const port = Number(process.argv[2])
const key = process.argv[3] || ''
const out = (data) => process.stdout.write(JSON.stringify(data) + '\n')

const socket = new WebSocket(lib.connectionUrl({ host: '127.0.0.1', port, key }))
const waiting = new Map() // command id -> action id
let started = false
let nextId = 1

function pressAll(state) {
	let current = '' // (the action being pressed)
	const send = (path, params) => {
		const id = nextId++
		waiting.set(id, current)
		socket.send(JSON.stringify({ type: 'command', id, command: { path, params } }))
	}
	const actions = lib.actionDefinitions(state, send)
	const overrides = {
		variable: { value: '0.5' },
		console_release_lights: { lights: (state.lights || []).slice(0, 1).map((light) => light.id) },
	}
	for (const [id, action] of Object.entries(actions)) {
		const options = {}
		for (const option of action.options) options[option.id] = option.default
		Object.assign(options, overrides[id] || {})
		current = id
		action.callback({ options })
	}
	out({ step: 'pressed', count: waiting.size })
	if (!waiting.size) socket.close()
}

socket.addEventListener('message', (event) => {
	const message = JSON.parse(String(event.data))
	if (message.type === 'state' && !started) {
		started = true
		const s = message.state
		out({ step: 'state', show: s.show, inputs: s.inputs.length, base: (s.base || {}).id, next: s.next, paused: s.paused })
		pressAll(s)
	} else if (message.type === 'result') {
		out({ step: 'result', action: waiting.get(message.id), ok: message.ok, status: message.status || 200 })
		waiting.delete(message.id)
		if (!waiting.size) socket.close(1000)
	}
})
socket.addEventListener('close', (event) => {
	out({ step: 'closed', code: event.code })
	process.exit(0)
})
socket.addEventListener('error', (event) => {
	out({ step: 'error', message: String((event && event.message) || 'the connection failed') })
})
setTimeout(() => {
	out({ step: 'timeout' })
	process.exit(2)
}, 15000)
