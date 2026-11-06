// The module itself (main.js), with a stand-in for Companion's library, against a pretend CentraLight: it connects,
// makes its actions and so on from the state, sends commands, keeps its variables current and reconnects by itself.
'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const Module = require('node:module')
const stub = require('./stub_base')
const { startFake } = require('./fake_centralight')
const sampleState = require('./sample_state')

const load = Module._load
Module._load = function (request, ...rest) {
	return request === '@companion-module/base' ? stub : load.call(this, request, ...rest)
}
require('../main')
const CentraLight = stub.entry.cls

function waitFor(check, what, seconds = 5) {
	return new Promise((resolve, reject) => {
		const started = Date.now()
		const timer = setInterval(() => {
			let value
			try {
				value = check()
			} catch (error) {
				value = undefined
			}
			if (value) {
				clearInterval(timer)
				resolve(value)
			} else if (Date.now() - started > seconds * 1000) {
				clearInterval(timer)
				reject(new Error(`timed out waiting for ${what}`))
			}
		}, 20)
	})
}

async function connected(t, config = {}) {
	const fake = await startFake(sampleState())
	const instance = new CentraLight()
	t.after(async () => {
		await instance.destroy()
		await fake.stop()
	})
	await instance.init({ host: '127.0.0.1', port: fake.port, key: '', ...config })
	await waitFor(() => instance.variables.show === 'Central Church', 'the first state')
	return { fake, instance }
}

test('it runs as a Companion module', () => {
	assert.equal(typeof CentraLight, 'function')
	assert.deepEqual(stub.entry.upgrades, [])
	const fields = new CentraLight().getConfigFields().map((field) => field.id)
	assert.deepEqual(fields, ['about', 'host', 'port', 'key'])
})

test('it connects, makes everything from the state and sends commands', async (t) => {
	const { fake, instance } = await connected(t)
	assert.ok(instance.statuses.some(([status]) => status === 'ok'))
	assert.deepEqual(fake.urls, ['/api/ws'])
	const inputs = instance.actions.base.options[0].choices.map((choice) => choice.id)
	assert.deepEqual(inputs, ['warm-white', 'worship-blue'])
	assert.equal(instance.variables.master, 80)
	assert.ok(instance.presets['base_worship-blue'])
	assert.equal(instance.feedbacks.on_base.callback({ options: { input: 'warm-white' } }), true)
	instance.actions.base.callback({ options: { input: 'worship-blue', how: 'cut', duration: 1 } })
	await waitFor(() => fake.commands.length === 1, 'the command')
	assert.deepEqual(fake.commands[0], { path: 'input/worship-blue/cut', params: {} })
})

test("an Overlay preset's key: the press sends the input, the release lets it go, in that order", async (t) => {
	const { fake, instance } = await connected(t)
	const step = instance.presets['overlay_worship-blue'].steps[0] // (M18: as a placed preset key does it)
	for (const action of [...step.down, ...step.up]) instance.actions[action.actionId].callback({ options: action.options })
	await waitFor(() => fake.commands.length === 2, 'both commands')
	assert.deepEqual(fake.commands, [
		{ path: 'input/worship-blue/overlay', params: {} },
		{ path: 'input/worship-blue/overlay/release', params: {} },
	])
})

test('it keeps up with CentraLight', async (t) => {
	const { fake, instance } = await connected(t)
	const defined = instance.defined
	const checked = instance.checked
	fake.push({ ...sampleState(), base: { id: 'worship-blue', name: 'Worship blue' }, master: 0.25 })
	await waitFor(() => instance.variables.master === 25, 'the new state')
	assert.equal(instance.variables.base, 'Worship blue')
	assert.ok(instance.checked > checked) // (the buttons' feedbacks were looked at again)
	assert.equal(instance.feedbacks.on_base.callback({ options: { input: 'worship-blue' } }), true)
	assert.equal(instance.defined, defined) // (same lists: the actions weren't made again)
	fake.push({ ...sampleState(), inputs: [{ id: 'candles', name: 'Candles' }] })
	await waitFor(() => instance.defined > defined, 'the new lists')
	assert.deepEqual(instance.actions.base.options[0].choices, [{ id: 'candles', label: 'Candles' }])
})

test('when CentraLight goes, the keys go dark (nothing left looking live)', async (t) => {
	const { fake, instance } = await connected(t)
	assert.equal(instance.feedbacks.on_base.callback({ options: { input: 'warm-white' } }), true)
	assert.equal(instance.feedbacks.connected.callback({ options: {} }), true)
	fake.drop()
	await waitFor(() => instance.variables.show === '', 'the variables emptied')
	assert.equal(instance.feedbacks.on_base.callback({ options: { input: 'warm-white' } }), false)
	assert.equal(instance.feedbacks.connected.callback({ options: {} }), false)
	assert.equal(instance.variables.master, '')
	assert.equal(instance.variables['copies_worship-blue'], '') // (every variable, not just the fixed ones)
	assert.equal(instance.variables.house_festoon, '')
	assert.equal(instance.variables.console_held, '')
})

test('a refusal at the cap says so, and an old CentraLight says it needs updating', async (t) => {
	for (const [refuse, status, words] of [
		[503, 'connection_failure', /too many/],
		[404, 'connection_failure', /needs updating/],
	]) {
		const fake = await startFake(sampleState())
		fake.refuse = refuse
		const instance = new CentraLight()
		t.after(async () => {
			await instance.destroy()
			await fake.stop()
		})
		await instance.init({ host: '127.0.0.1', port: fake.port, key: '' })
		await waitFor(() => instance.statuses.some(([s, text]) => s === status && words.test(text || '')), `${refuse}`)
	}
})

test('a connection that never opens is given up and tried again', async (t) => {
	const net = require('node:net')
	const taken = new Set()
	const silent = net.createServer((socket) => taken.add(socket)) // (takes the connection, never answers)
	await new Promise((resolve) => silent.listen(0, '127.0.0.1', resolve))
	const instance = new CentraLight()
	t.after(async () => {
		await instance.destroy()
		for (const socket of taken) socket.destroy()
		await new Promise((resolve) => silent.close(() => resolve()))
	})
	await instance.init({ host: '127.0.0.1', port: silent.address().port, key: '' })
	await waitFor(() => instance.statuses.filter(([s]) => s === 'connecting').length >= 2, 'a second try', 9)
})

test('a refusal says why: the key, or too many connections', async (t) => {
	const fake = await startFake(sampleState())
	fake.refuse = 403
	const instance = new CentraLight()
	t.after(async () => {
		await instance.destroy()
		await fake.stop()
	})
	await instance.init({ host: '127.0.0.1', port: fake.port, key: 'wrong' })
	await waitFor(() => instance.statuses.some(([status]) => status === 'authentication_failure'), 'the key problem')
	assert.match(instance.statuses.find(([status]) => status === 'authentication_failure')[1], /key/)
	fake.refuse = 0 // (the key put right in CentraLight: it connects by itself)
	await waitFor(() => instance.variables.show === 'Central Church', 'connecting once allowed', 8)
})

test('an address that is no address is a bad setting, not a retry for ever', async (t) => {
	const instance = new CentraLight()
	t.after(() => instance.destroy())
	await instance.init({ host: 'my pc', port: 8746, key: '' })
	assert.equal(instance.statuses.at(-1)[0], 'bad_config')
	assert.equal(instance.retryTimer, null)
})

test('renaming the connection makes everything again (the presets name it)', async (t) => {
	const { fake, instance } = await connected(t)
	const defined = instance.defined
	instance.label = 'Lights'
	await instance.configUpdated({ host: '127.0.0.1', port: fake.port, key: '' })
	assert.ok(instance.defined > defined)
	assert.equal(instance.variables.show, '') // (the old state doesn't look live while it reconnects)
	await waitFor(() => instance.presets['overlay_worship-blue'], 'the state again')
	assert.match(instance.presets['overlay_worship-blue'].style.text, /\$\(Lights:copies_worship-blue\)/)
})

test('a refused command is logged', async (t) => {
	const { fake, instance } = await connected(t)
	fake.answer = { ok: false, error: "there's no input called 'x'", status: 404 }
	instance.actions.overlay.callback({ options: { input: 'x' } })
	await waitFor(() => instance.logs.some(([level, text]) => level === 'warn' && text.includes("no input called 'x'")), 'the warning')
})

test('it reconnects by itself, and not once it is removed', async (t) => {
	const { fake, instance } = await connected(t)
	fake.drop()
	await waitFor(() => instance.statuses.some(([status]) => status === 'disconnected'), 'noticing')
	await waitFor(() => fake.clients.size === 1, 'reconnecting', 8)
	await waitFor(() => instance.statuses.at(-1)[0] === 'ok', 'connected again')
	instance.actions.overlay_clear.callback({ options: {} })
	await waitFor(() => fake.commands.length === 1, 'a command after reconnecting')
	await instance.destroy()
	await waitFor(() => fake.clients.size === 0, 'closing')
	const before = fake.urls.length
	await new Promise((resolve) => setTimeout(resolve, 1500))
	assert.equal(fake.urls.length, before) // (removed: no more tries)
})

test('a button while not connected does nothing but say so', async (t) => {
	const fake = await startFake(sampleState())
	const port = fake.port
	await fake.stop()
	const instance = new CentraLight()
	t.after(() => instance.destroy())
	await instance.init({ host: '127.0.0.1', port, key: '' })
	await waitFor(() => instance.statuses.some(([status]) => status !== 'connecting'), 'giving up on this try')
	instance.actions.overlay_clear.callback({ options: {} })
	assert.ok(instance.logs.some(([level, text]) => level === 'warn' && text.includes('not connected')))
})

test('the key from another computer goes in the address, and a new address reconnects', async (t) => {
	const { fake, instance } = await connected(t, { key: 'k 1' })
	assert.deepEqual(fake.urls, ['/api/ws?key=k%201'])
	const other = await startFake({ ...sampleState(), show: 'Evening' })
	t.after(() => other.stop())
	await instance.configUpdated({ host: '127.0.0.1', port: other.port, key: '' })
	await waitFor(() => instance.variables.show === 'Evening', 'the other CentraLight')
	await waitFor(() => fake.clients.size === 0, 'the old connection closing')
})
