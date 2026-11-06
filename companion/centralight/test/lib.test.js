// The module's own logic: the URL, what each action sends, the feedbacks, variables and presets.
'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const lib = require('../lib')
const sampleState = require('./sample_state')

const combine = (r, g, b) => ((r & 0xff) << 16) | ((g & 0xff) << 8) | (b & 0xff)

test('the address of the live connection', () => {
	assert.equal(lib.connectionUrl({}), 'ws://127.0.0.1:8746/api/ws')
	assert.equal(lib.connectionUrl({ host: ' 192.168.1.20 ', port: 9000, key: ' a b ' }), 'ws://192.168.1.20:9000/api/ws?key=a%20b')
	assert.equal(lib.connectionUrl({ host: '::1', port: '8746' }), 'ws://[::1]:8746/api/ws')
	assert.equal(lib.connectionUrl({ host: '', port: 'nonsense' }), 'ws://127.0.0.1:8746/api/ws')
	// (a host typed as an address, with its port, or with a scheme: understood)
	assert.equal(lib.connectionUrl({ host: '127.0.0.1:9000', port: 8746 }), 'ws://127.0.0.1:9000/api/ws')
	assert.equal(lib.connectionUrl({ host: 'http://192.168.1.20:8746/', port: 1 }), 'ws://192.168.1.20:8746/api/ws')
	assert.equal(lib.connectionUrl({ host: '[::1]:9000' }), 'ws://[::1]:9000/api/ws')
	assert.equal(lib.askUrl({ host: 'church-pc', port: 8746, key: 'k' }), 'http://church-pc:8746/api/ws?key=k')
	assert.throws(() => lib.connectionUrl({ host: 'my pc', port: 8746 }), /isn't a PC's address/)
	assert.throws(() => lib.connectionUrl({ host: 'pc', port: 70000 }), /isn't a PC's address/)
})

test('the countdown action names the countdown showing now', () => {
	const sent = []
	let state = { ...sampleState(), countdown: { id: 'c7', text: 'Loading Evening', seconds: 9 } }
	const actions = lib.actionDefinitions(state, (path, params) => sent.push([path, params]), () => state)
	actions.countdown.callback({ options: { state: 'now' } })
	assert.deepEqual(sent.pop(), ['countdown/now', { id: 'c7' }])
	state = sampleState() // (gone: nothing to name)
	actions.countdown.callback({ options: { state: 'skip' } })
	assert.deepEqual(sent.pop(), ['countdown/skip', {}])
})

test('a switch variable (sent as 1 or 0) matches on and off', () => {
	assert.equal(lib.variableMatches(1, 'on', 'equals', 'switch'), true)
	assert.equal(lib.variableMatches(1, 'true', 'equals', 'switch'), true)
	assert.equal(lib.variableMatches(0, 'off', 'equals', 'switch'), true)
	assert.equal(lib.variableMatches(1, 'off', 'equals', 'switch'), false)
	assert.equal(lib.variableMatches(0, 'on', 'equals', 'switch'), false)
	assert.equal(lib.variableMatches(0.5, '0.5', 'equals', 'number'), true)
	assert.equal(lib.variableMatches(0.7, '0.5', 'above', 'number'), true)
})

test('the connected feedback', () => {
	let connected = false
	const feedbacks = lib.feedbackDefinitions(() => sampleState(), combine, () => connected)
	assert.equal(feedbacks.connected.callback({ options: {} }), false)
	connected = true
	assert.equal(feedbacks.connected.callback({ options: {} }), true)
	assert.equal(feedbacks.connected.showInvert, true)
})

test('each action sends what its Stream Deck URL does', () => {
	const sent = []
	const actions = lib.actionDefinitions(sampleState(), (path, params) => sent.push([path, params]))
	const press = (id, options) => {
		actions[id].callback({ options })
		return sent.pop()
	}
	assert.deepEqual(press('base', { input: 'warm-white', how: 'own', duration: 1 }), ['input/warm-white', {}])
	assert.deepEqual(press('base', { input: 'warm-white', how: 'cut', duration: 1 }), ['input/warm-white/cut', {}])
	assert.deepEqual(press('base', { input: 'warm-white', how: 'wipe', duration: 2.5 }), [
		'input/warm-white',
		{ transition: 'wipe', duration: '2.5' },
	])
	assert.deepEqual(press('overlay', { input: 'worship-blue' }), ['input/worship-blue/overlay', {}])
	assert.deepEqual(press('overlay_release', { input: 'worship-blue' }), ['input/worship-blue/overlay/release', {}])
	assert.deepEqual(press('overlay_clear_input', { input: 'worship-blue' }), ['input/worship-blue/overlay/clear', {}])
	assert.deepEqual(press('overlay_clear', {}), ['overlay/clear', {}])
	assert.deepEqual(press('overlay_switch', { state: 'off' }), ['overlay/off', {}])
	assert.deepEqual(press('show', { show: 'evening' }), ['show/evening', {}])
	assert.deepEqual(press('black', { how: 'cut', all: true }), ['black/cut', { all: '1' }])
	assert.deepEqual(press('black', { how: 'fade', all: false }), ['black/fade', {}])
	assert.deepEqual(press('master', { level: 80, fade: 2 }), ['master', { level: '0.8', fade: '2' }])
	assert.deepEqual(press('house', { light: 'dimmer-4', state: 'level', level: 40, fade: 3 }), [
		'house/dimmer-4',
		{ level: '0.4', fade: '3' },
	])
	assert.deepEqual(press('house', { light: 'dimmer-4', state: 'toggle', level: 40, fade: 3 }), [
		'house/dimmer-4/toggle',
		{ fade: '3' },
	])
	assert.deepEqual(press('variable', { name: 'Pulse speed', value: ' 0.7 ' }), ['var/Pulse%20speed', { value: '0.7' }])
	assert.deepEqual(press('automation', { automation: 'show:end-of-service', do: 'run' }), [
		'automation/end-of-service/run',
		{},
	])
	assert.deepEqual(press('automation', { automation: 'global:sunday-morning', do: 'disable' }), [
		'global-automation/sunday-morning/disable',
		{},
	])
	assert.deepEqual(press('automations_pause', { state: 'pause' }), ['automations/pause', {}])
	assert.deepEqual(press('countdown', { state: 'skip' }), ['countdown/skip', {}])
	assert.deepEqual(press('console_release', {}), ['live/release', {}])
	assert.deepEqual(press('console_release_lights', { lights: ['slimpar-101', 'slimpar-102'] }), [
		'live/release',
		{ lights: 'slimpar-101,slimpar-102' },
	])
	actions.console_release_lights.callback({ options: { lights: [] } })
	assert.equal(sent.length, 0) // (no lights chosen: nothing sent)
	assert.deepEqual(press('console_tap', {}), ['live/tap', {}])
	assert.deepEqual(press('console_next', {}), ['live/next', {}])
	assert.deepEqual(press('console_pause', { state: 'resume' }), ['live/resume', {}])
	assert.deepEqual(press('console_speed', { bpm: 96 }), ['live/speed', { bpm: '96' }])
})

test("the dropdowns list the show's inputs, shows, lights and automations", () => {
	const actions = lib.actionDefinitions(sampleState(), () => {})
	const ids = (action, option) => actions[action].options.find((o) => o.id === option).choices.map((c) => c.id)
	assert.deepEqual(ids('base', 'input'), ['warm-white', 'worship-blue'])
	assert.equal(actions.base.options[0].default, 'warm-white')
	assert.deepEqual(ids('show', 'show'), ['central-church', 'evening'])
	assert.deepEqual(ids('house', 'light'), ['dimmer-4', 'dimmer-3'])
	assert.deepEqual(ids('variable', 'name'), ['Pulse speed', 'accent', 'party'])
	assert.deepEqual(ids('automation', 'automation'), ['show:end-of-service', 'global:sunday-morning'])
	assert.deepEqual(ids('console_release_lights', 'lights'), ['slimpar-101', 'slimpar-102'])
	const empty = lib.actionDefinitions({}, () => {})
	assert.equal(empty.base.options[0].default, '') // (not connected yet: empty lists, nothing breaks)
})

test('the feedbacks', () => {
	let state = sampleState()
	const feedbacks = lib.feedbackDefinitions(() => state, combine)
	const on = (id, options = {}) => feedbacks[id].callback({ options })
	assert.equal(on('on_base', { input: 'warm-white' }), true)
	assert.equal(on('on_base', { input: 'worship-blue' }), false)
	assert.equal(on('on_overlay', { input: 'worship-blue' }), true)
	assert.equal(on('on_overlay', { input: 'warm-white' }), false)
	assert.equal(on('show_loaded', { show: 'central-church' }), true)
	assert.equal(on('show_loaded', { show: 'evening' }), false)
	assert.equal(on('black'), false)
	assert.equal(on('countdown'), false)
	assert.equal(on('paused'), false)
	assert.equal(on('house_on', { light: 'dimmer-4' }), true)
	assert.equal(on('house_on', { light: 'dimmer-3' }), false)
	assert.equal(on('variable', { name: 'Pulse speed', compare: 'equals', value: '0.5' }), true)
	assert.equal(on('variable', { name: 'Pulse speed', compare: 'above', value: '0.4' }), true)
	assert.equal(on('variable', { name: 'Pulse speed', compare: 'below', value: '0.4' }), false)
	assert.equal(on('variable', { name: 'accent', compare: 'equals', value: '#ff0000' }), true)
	assert.equal(on('variable', { name: 'party', compare: 'equals', value: 'off' }), true)
	assert.equal(on('variable', { name: 'party', compare: 'equals', value: 'on' }), false)
	assert.equal(on('variable', { name: 'nobody', compare: 'equals', value: '' }), false)
	assert.equal(on('console_holds'), false)
	// (the feedbacks read the state as it is now, not as it was when they were made)
	state = { ...state, overlay: { ...state.overlay, on: false }, colour_black: true, paused: true, console: { held: 3, lights: [] } }
	state.countdown = { id: 'c1', text: 'Loading Evening', seconds: 12 }
	assert.equal(on('on_overlay', { input: 'worship-blue' }), false) // (switched off: hidden)
	assert.equal(on('black'), true)
	assert.equal(on('paused'), true)
	assert.equal(on('countdown'), true)
	assert.equal(on('console_holds'), true)
	state = { ...state, no_show: true }
	assert.equal(on('show_loaded', { show: 'central-church' }), false)
})

test('the variables', () => {
	const state = sampleState()
	const ids = lib.variableDefinitions(state).map((v) => v.variableId)
	for (const id of ['show', 'base', 'master', 'countdown', 'next', 'console_held', 'house_festoon', 'copies_worship-blue']) {
		assert.ok(ids.includes(id), id)
	}
	const values = lib.variableValues(state)
	assert.equal(values.show, 'Central Church')
	assert.equal(values.base, 'Warm white')
	assert.equal(values.master, 80)
	assert.equal(values.countdown, '')
	assert.equal(values.next, 'Sun 09:30 Sunday morning')
	assert.equal(values.house_festoon, 40)
	assert.equal(values['house_key-lights'], 0) // (switched off)
	assert.equal(values['copies_worship-blue'], 2)
	assert.equal(values['copies_warm-white'], 0)
	const later = lib.variableValues({ ...state, no_show: true, countdown: { seconds: 14.2, text: 'Loading Evening' } })
	assert.equal(later.show, 'No show')
	assert.equal(later.countdown, 15)
	assert.equal(later.countdown_text, 'Loading Evening')
	assert.deepEqual(Object.keys(lib.variableValues({})).sort(), [
		'base',
		'console_held',
		'countdown',
		'countdown_text',
		'master',
		'next',
		'show',
	])
})

test('the presets', () => {
	const presets = lib.presetDefinitions(sampleState(), combine, 'CentraLight PC')
	assert.ok(presets['base_warm-white'])
	assert.equal(presets['base_warm-white'].steps[0].down[0].actionId, 'base')
	assert.equal(presets['overlay_worship-blue'].style.text, 'Worship blue\\n+$(CentraLight PC:copies_worship-blue)')
	// (M18: the key's release lets go of an input set to "Only while held"; the old flash keys went, the input says)
	const step = presets['overlay_worship-blue'].steps[0]
	assert.deepEqual(step.down, [{ actionId: 'overlay', options: { input: 'worship-blue' } }])
	assert.deepEqual(step.up, [{ actionId: 'overlay_release', options: { input: 'worship-blue' } }])
	assert.equal(Object.keys(presets).filter((id) => id.startsWith('flash_')).length, 0)
	assert.ok(presets.show_evening)
	for (const id of ['black_cut', 'black_fade', 'black_cut_all', 'black_fade_all', 'console_release', 'console_tap']) {
		assert.ok(presets[id], id)
	}
	assert.equal(presets.black_fade_all.steps[0].down[0].options.all, true)
	const actions = lib.actionDefinitions(sampleState(), () => {})
	const feedbacks = lib.feedbackDefinitions(() => sampleState(), combine)
	for (const [id, preset] of Object.entries(presets)) {
		assert.equal(preset.type, 'button', id)
		for (const step of preset.steps) {
			for (const action of [...step.down, ...step.up]) assert.ok(actions[action.actionId], `${id}: ${action.actionId}`)
		}
		for (const feedback of preset.feedbacks) assert.ok(feedbacks[feedback.feedbackId], `${id}: ${feedback.feedbackId}`)
	}
})

test('the lists key changes only when a dropdown would', () => {
	const state = sampleState()
	const key = lib.listsKey(state)
	assert.equal(lib.listsKey({ ...state, master: 0.2, base: { id: 'worship-blue', name: 'Worship blue' } }), key)
	assert.notEqual(lib.listsKey({ ...state, inputs: [...state.inputs, { id: 'new', name: 'New' }] }), key)
	assert.notEqual(lib.listsKey({ ...state, lights: [] }), key)
})
