// CentraLight for Companion: everything that doesn't need Companion itself, so it can be tested on its own.
// The state comes from CentraLight's live connection (/api/ws). Commands go back the same way, as the paths of its
// Stream Deck URLs: {"path": "input/warm-white", "params": {...}} does what /api/input/warm-white does.

'use strict'

const CORAL = [255, 111, 89] // on Base (as on the Dashboard)
const LIME = [214, 255, 121] // on the Overlay
const RED = [255, 59, 92] // black: Cut / Fade
const AMBER = [255, 181, 71] // a countdown, automations paused, the Console holding lights
const SKY = [134, 214, 255] // a show loaded, a house light on, a variable
const GREEN = [61, 220, 132] // connected
const WHITE = [255, 255, 255]
const BLACK = [0, 0, 0]

const DEFAULT_PORT = 8746

const TRANSITIONS = [
	{ id: 'fade', label: 'Fade' },
	{ id: 'dip', label: 'Dip (to black, then up)' },
	{ id: 'wipe', label: 'Wipe' },
	{ id: 'iris', label: 'Iris' },
	{ id: 'random', label: 'Random (each light at its own moment)' },
]

// The connection's settings as an address. A host typed with "http://", a path or its own ":port" is understood; one
// that still isn't a PC's address throws (Companion shows that as a bad setting).
function address(config) {
	const typed = String((config && config.host) || '').trim()
	let host = typed.replace(/^[a-z][a-z0-9+.-]*:\/\//i, '').replace(/[/?#].*$/, '')
	let port = Math.round(Number(config && config.port)) || DEFAULT_PORT
	const withPort = /^(\[[^\]]+\]|[^:]+):(\d+)$/.exec(host)
	if (withPort) {
		host = withPort[1]
		port = Number(withPort[2])
	}
	host = host || '127.0.0.1'
	if (host.includes(':') && !host.startsWith('[')) host = `[${host}]` // (an IPv6 address)
	const problem = `"${typed}" isn't a PC's address: use its name or numbers, e.g. 127.0.0.1 for this PC`
	if (!(port > 0 && port < 65536) || /\s/.test(host)) throw new Error(problem)
	try {
		new URL(`ws://${host}:${port}/`)
	} catch (error) {
		throw new Error(problem)
	}
	return { host, port, key: String((config && config.key) || '').trim() }
}

function connectionUrl(config) {
	const { host, port, key } = address(config)
	return `ws://${host}:${port}/api/ws` + (key ? `?key=${encodeURIComponent(key)}` : '')
}

// What a failed connection asks, to say why: the live connection's own address as a plain web page answers with the
// reason (403: the key; 503: too many connections; 404: a CentraLight too old to have it).
function askUrl(config) {
	const { host, port, key } = address(config)
	return `http://${host}:${port}/api/ws` + (key ? `?key=${encodeURIComponent(key)}` : '')
}

// A path for CentraLight: each part made safe for a URL ("var/Pulse speed" -> "var/Pulse%20speed").
function path(...parts) {
	return parts.map((part) => encodeURIComponent(String(part))).join('/')
}

function choices(list, label = (item) => item.name, id = (item) => item.id) {
	return (list || []).map((item) => ({ id: id(item), label: label(item) }))
}

function firstId(list) {
	return list && list.length ? list[0].id : ''
}

// What the dropdowns list: when this changes, the definitions are made again.
function listsKey(state) {
	const s = state || {}
	return JSON.stringify([
		(s.inputs || []).map((i) => [i.id, i.name]),
		(s.shows || []).map((i) => [i.id, i.name]),
		(s.house || []).map((i) => [i.id, i.name]),
		(s.globals || []).map((i) => [i.name, i.type]),
		(s.automations || []).map((i) => [i.id, i.name]),
		(s.global_automations || []).map((i) => [i.id, i.name]),
		(s.lights || []).map((i) => [i.id, i.name]),
	])
}

function rgb(colour, combine) {
	return combine(colour[0], colour[1], colour[2])
}

function dropdown(id, label, list, extra = {}) {
	return { type: 'dropdown', id, label, choices: list, default: firstId(list), ...extra }
}

// ---------------------------------------------------------------- actions

function actionDefinitions(state, send, getState = () => state) {
	const s = state || {}
	const inputs = choices(s.inputs)
	const shows = choices(s.shows)
	const house = choices(s.house)
	const lights = choices(s.lights)
	const globals = choices(s.globals, (g) => g.name, (g) => g.name)
	const automations = [
		...choices(s.automations, (a) => `This show: ${a.name}`, (a) => `show:${a.id}`),
		...choices(s.global_automations, (a) => `Global: ${a.name}`, (a) => `global:${a.id}`),
	]
	const run = (where, params) => send(where, params || {})
	const seconds = (id, label, value) => ({ type: 'number', id, label, default: value, min: 0, max: 60, step: 0.1 })
	return {
		base: {
			name: 'Send an input to Base',
			options: [
				dropdown('input', 'Input', inputs),
				dropdown('how', 'Transition', [{ id: 'own', label: 'Its own' }, { id: 'cut', label: 'Cut' }, ...TRANSITIONS], {
					default: 'own',
				}),
				seconds('duration', 'Seconds (for a transition chosen here)', 1),
			],
			callback: (action) => {
				const o = action.options
				if (o.how === 'cut') return run(path('input', o.input, 'cut'))
				if (o.how === 'own') return run(path('input', o.input))
				return run(path('input', o.input), { transition: o.how, duration: String(o.duration) })
			},
		},
		overlay: {
			name: 'Send an input to the Overlay (a copy on top)',
			description:
				'As the input says in CentraLight: its copy stays until it is cleared, or, "Only while held", until ' +
				'"Release (held inputs)" lets it go: put that on the key\'s release (the presets do)',
			options: [dropdown('input', 'Input', inputs)],
			callback: (action) => run(path('input', action.options.input, 'overlay')),
		},
		// (M18: the let-go of a key that sent an input set to "Only while held"; for one whose copies stay, nothing)
		overlay_release: {
			name: 'Release (held inputs): an input sent to the Overlay "Only while held" goes',
			description: "Put it on the release of the key that sends the input. Inputs whose copies stay don't mind it.",
			options: [dropdown('input', 'Input', inputs)],
			callback: (action) => run(path('input', action.options.input, 'overlay', 'release')),
		},
		overlay_clear_input: {
			name: "Clear an input's copies from the Overlay",
			options: [dropdown('input', 'Input', inputs)],
			callback: (action) => run(path('input', action.options.input, 'overlay', 'clear')),
		},
		// (14 Nov: one key turns an input on and off on the Overlay, going by what CentraLight shows, so it can't get out
		// of step when the copy is cleared in CentraLight itself)
		overlay_toggle: {
			name: 'Toggle an input on the Overlay (on if it is off, cleared if it is on)',
			options: [dropdown('input', 'Input', inputs)],
			callback: (action) => {
				const input = action.options.input
				const shown = copiesOf(getState(), input) > 0
				return run(shown ? path('input', input, 'overlay', 'clear') : path('input', input, 'overlay'))
			},
		},
		overlay_clear: { name: 'Clear the Overlay (every copy)', options: [], callback: () => run('overlay/clear') },
		overlay_switch: {
			name: "The Overlay's switch (off hides it without emptying it)",
			options: [
				dropdown('state', 'Do', [
					{ id: 'toggle', label: 'Switch on / off' },
					{ id: 'on', label: 'Switch on' },
					{ id: 'off', label: 'Switch off' },
				]),
			],
			callback: (action) => run(path('overlay', action.options.state)),
		},
		show: {
			name: 'Load a show',
			options: [dropdown('show', 'Show', shows)],
			callback: (action) => run(path('show', action.options.show)),
		},
		black: {
			name: 'Cut / Fade to black',
			options: [
				dropdown('how', 'How', [
					{ id: 'fade', label: 'Fade (as long as Settings says)' },
					{ id: 'cut', label: 'Cut' },
				]),
				{ type: 'checkbox', id: 'all', label: 'Everything: the Festoon and Key lights too', default: false },
			],
			callback: (action) => run(path('black', action.options.how), action.options.all ? { all: '1' } : {}),
		},
		master: {
			name: 'Master',
			options: [
				{ type: 'number', id: 'level', label: 'Level %', default: 100, min: 0, max: 100 },
				seconds('fade', 'Fade (seconds)', 0),
			],
			callback: (action) =>
				run('master', { level: String(action.options.level / 100), fade: String(action.options.fade) }),
		},
		house: {
			name: 'The Festoon or Key lights',
			options: [
				dropdown('light', 'Light', house),
				dropdown('state', 'Do', [
					{ id: 'level', label: 'Set the level (switches it on)' },
					{ id: 'toggle', label: 'Switch on / off' },
					{ id: 'on', label: 'Switch on' },
					{ id: 'off', label: 'Switch off' },
				]),
				{ type: 'number', id: 'level', label: 'Level %', default: 70, min: 0, max: 100 },
				seconds('fade', 'Fade (seconds)', 2),
			],
			callback: (action) => {
				const o = action.options
				if (o.state === 'level') {
					return run(path('house', o.light), { level: String(o.level / 100), fade: String(o.fade) })
				}
				return run(path('house', o.light, o.state), { fade: String(o.fade) })
			},
		},
		variable: {
			name: 'Set a show variable',
			options: [
				dropdown('name', 'Variable', globals),
				{
					type: 'textinput',
					id: 'value',
					label: 'Value: a number, a colour like FF6F59, or on / off / toggle',
					default: '',
				},
			],
			callback: (action) => run(path('var', action.options.name), { value: String(action.options.value).trim() }),
		},
		automation: {
			name: 'Run, turn on or turn off an automation',
			options: [
				dropdown('automation', 'Automation', automations),
				dropdown('do', 'Do', [
					{ id: 'run', label: 'Run it now' },
					{ id: 'enable', label: 'Turn it on' },
					{ id: 'disable', label: 'Turn it off' },
				]),
			],
			callback: (action) => {
				const value = String(action.options.automation)
				const at = value.indexOf(':')
				const scope = value.slice(0, at)
				return run(path(scope === 'global' ? 'global-automation' : 'automation', value.slice(at + 1), action.options.do))
			},
		},
		automations_pause: {
			name: 'Pause or resume all automations',
			options: [
				dropdown('state', 'Do', [
					{ id: 'toggle', label: 'Pause / resume' },
					{ id: 'pause', label: 'Pause' },
					{ id: 'resume', label: 'Resume' },
				]),
			],
			callback: (action) => run(path('automations', action.options.state)),
		},
		countdown: {
			name: 'The countdown before an automatic change',
			options: [
				dropdown('state', 'Do', [
					{ id: 'now', label: 'Do it now' },
					{ id: 'wait', label: 'Wait 10 minutes' },
					{ id: 'skip', label: 'Skip it this time' },
				]),
			],
			callback: (action) => {
				// (it names the countdown showing now, as the phone page does: a second press can't hit the next one)
				const countdown = (getState() || {}).countdown
				return run(path('countdown', action.options.state), countdown && countdown.id ? { id: String(countdown.id) } : {})
			},
		},
		console_release: {
			name: 'Console: Release all (its lights fade back to the inputs)',
			options: [],
			callback: () => run('live/release'),
		},
		console_release_lights: {
			name: 'Console: Release some lights',
			options: [{ type: 'multidropdown', id: 'lights', label: 'Lights', choices: lights, default: [] }],
			callback: (action) => {
				const chosen = (action.options.lights || []).filter(Boolean)
				if (chosen.length) return run('live/release', { lights: chosen.join(',') })
			},
		},
		console_tap: { name: "Console: Tap (the chase's speed)", options: [], callback: () => run('live/tap') },
		console_next: { name: "Console: the chase's next step", options: [], callback: () => run('live/next') },
		console_pause: {
			name: 'Console: pause or play on the chase',
			options: [
				dropdown('state', 'Do', [
					{ id: 'pause', label: 'Pause' },
					{ id: 'resume', label: 'Play on' },
				]),
			],
			callback: (action) => run(path('live', action.options.state)),
		},
		console_speed: {
			name: "Console: the chase's speed",
			options: [{ type: 'number', id: 'bpm', label: 'Beats a minute (a step each beat)', default: 120, min: 10, max: 300 }],
			callback: (action) => run('live/speed', { bpm: String(action.options.bpm) }),
		},
	}
}

// ---------------------------------------------------------------- feedbacks

function copiesOf(state, inputId) {
	const overlay = (state && state.overlay) || {}
	return (overlay.copies || []).filter((copy) => copy.id === inputId).length
}

const TRUE_WORDS = ['on', 'true', '1', 'yes']

// type: the show variable's ("number", "colour" or "switch": CentraLight sends a switch as 1 or 0)
function variableMatches(value, wanted, compare, type) {
	const number = Number(value)
	const word = String(wanted).trim().toLowerCase()
	const target = Number(word)
	const numbers = typeof value === 'number' && word !== '' && Number.isFinite(target)
	if (compare === 'above') return numbers && number > target
	if (compare === 'below') return numbers && number < target
	if (typeof value === 'boolean' || type === 'switch') {
		const on = typeof value === 'boolean' ? value : Number.isFinite(number) && number >= 0.5
		return on === TRUE_WORDS.includes(word)
	}
	if (numbers) return Math.abs(number - target) < 1e-6
	return String(value).trim().replace(/^#/, '').toLowerCase() === String(wanted).trim().replace(/^#/, '').toLowerCase()
}

function feedbackDefinitions(getState, combine, isConnected = () => Object.keys(getState() || {}).length > 0) {
	const s = getState() || {}
	const inputs = choices(s.inputs)
	const shows = choices(s.shows)
	const house = choices(s.house)
	const globals = choices(s.globals, (g) => g.name, (g) => g.name)
	const style = (fg, bg) => ({ color: rgb(fg, combine), bgcolor: rgb(bg, combine) })
	const now = () => getState() || {}
	return {
		on_base: {
			type: 'boolean',
			name: 'An input is on Base',
			defaultStyle: style(BLACK, CORAL),
			options: [dropdown('input', 'Input', inputs)],
			callback: (feedback) => (now().base || {}).id === feedback.options.input,
		},
		on_overlay: {
			type: 'boolean',
			name: 'An input is on the Overlay',
			defaultStyle: style(BLACK, LIME),
			options: [dropdown('input', 'Input', inputs)],
			callback: (feedback) => copiesOf(now(), feedback.options.input) > 0 && (now().overlay || {}).on !== false,
		},
		show_loaded: {
			type: 'boolean',
			name: 'A show is loaded',
			defaultStyle: style(BLACK, SKY),
			options: [dropdown('show', 'Show', shows)],
			callback: (feedback) => !now().no_show && now().show_id === feedback.options.show,
		},
		black: {
			type: 'boolean',
			name: 'The colour lights are black',
			defaultStyle: style(WHITE, RED),
			options: [],
			callback: () => now().colour_black === true,
		},
		countdown: {
			type: 'boolean',
			name: 'A countdown is running (before an automatic change)',
			defaultStyle: style(BLACK, AMBER),
			options: [],
			callback: () => Boolean(now().countdown),
		},
		paused: {
			type: 'boolean',
			name: 'Automations are paused',
			defaultStyle: style(BLACK, AMBER),
			options: [],
			callback: () => now().paused === true,
		},
		house_on: {
			type: 'boolean',
			name: 'The Festoon or Key lights are on',
			defaultStyle: style(BLACK, SKY),
			options: [dropdown('light', 'Light', house)],
			callback: (feedback) => Boolean(((now().levels || {})[feedback.options.light] || {}).on),
		},
		variable: {
			type: 'boolean',
			name: "A show variable's value",
			defaultStyle: style(BLACK, SKY),
			options: [
				dropdown('name', 'Variable', globals),
				dropdown('compare', 'Is', [
					{ id: 'equals', label: 'equal to' },
					{ id: 'above', label: 'above' },
					{ id: 'below', label: 'below' },
				]),
				{ type: 'textinput', id: 'value', label: 'Value', default: '' },
			],
			callback: (feedback) => {
				const values = now().variables || {}
				if (!Object.prototype.hasOwnProperty.call(values, feedback.options.name)) return false
				const known = (now().globals || []).find((g) => g.name === feedback.options.name)
				return variableMatches(values[feedback.options.name], feedback.options.value, feedback.options.compare,
					known && known.type)
			},
		},
		connected: {
			type: 'boolean',
			name: 'CentraLight is connected (invert it for a "not connected" warning)',
			defaultStyle: style(BLACK, GREEN),
			showInvert: true,
			options: [],
			callback: () => isConnected(),
		},
		console_holds: {
			type: 'boolean',
			name: 'The Console holds lights',
			defaultStyle: style(BLACK, AMBER),
			options: [],
			callback: () => ((now().console || {}).held || 0) > 0,
		},
	}
}

// ---------------------------------------------------------------- variables

function variableDefinitions(state) {
	const s = state || {}
	const list = [
		{ variableId: 'show', name: 'The show loaded' },
		{ variableId: 'base', name: "What's on Base" },
		{ variableId: 'master', name: 'Master %' },
		{ variableId: 'countdown', name: "The countdown's seconds (empty with none)" },
		{ variableId: 'countdown_text', name: 'What the countdown will do' },
		{ variableId: 'next', name: 'The next timed automation' },
		{ variableId: 'console_held', name: 'Lights the Console holds' },
	]
	for (const h of s.house || []) list.push({ variableId: `house_${h.slug || h.id}`, name: `${h.name} %` })
	for (const i of s.inputs || []) list.push({ variableId: `copies_${i.id}`, name: `${i.name}: copies on the Overlay` })
	return list
}

function variableValues(state) {
	const s = state || {}
	const values = {
		show: s.no_show ? 'No show' : s.show || '',
		base: (s.base || {}).name || '',
		master: typeof s.master === 'number' ? Math.round(s.master * 100) : '',
		countdown: s.countdown ? Math.ceil(Number(s.countdown.seconds) || 0) : '',
		countdown_text: s.countdown ? s.countdown.text || '' : '',
		next: s.next || '',
		console_held: s.console ? s.console.held || 0 : '',
	}
	for (const h of s.house || []) {
		const level = (s.levels || {})[h.id]
		values[`house_${h.slug || h.id}`] = level ? (level.on ? Math.round(level.level * 100) : 0) : ''
	}
	for (const i of s.inputs || []) values[`copies_${i.id}`] = copiesOf(s, i.id)
	return values
}

// ---------------------------------------------------------------- presets

function presetDefinitions(state, combine, label = 'centralight') {
	const s = state || {}
	const presets = {}
	const style = (text, fg, bg) => ({ text, size: 'auto', color: rgb(fg, combine), bgcolor: rgb(bg, combine) })
	const lit = (feedbackId, options, fg, bg) => [{ feedbackId, options, style: { color: rgb(fg, combine), bgcolor: rgb(bg, combine) } }]
	const press = (actionId, options) => [{ down: [{ actionId, options }], up: [] }]
	for (const i of s.inputs || []) {
		presets[`base_${i.id}`] = {
			type: 'button',
			category: 'Inputs to Base',
			name: i.name,
			style: style(i.name, WHITE, BLACK),
			steps: press('base', { input: i.id, how: 'own', duration: 1 }),
			feedbacks: lit('on_base', { input: i.id }, BLACK, CORAL),
		}
		// (M18: as the input's Send to Overlay setting says. Let go, Release: an "Only while held" input goes, so the same
		// key flashes it; one whose copies stay keeps its copy. The old "flash while held" keys went: the input says now)
		presets[`overlay_${i.id}`] = {
			type: 'button',
			category: 'Inputs to the Overlay',
			name: `${i.name} on the Overlay`,
			style: style(`${i.name}\\n+$(${label}:copies_${i.id})`, WHITE, BLACK),
			steps: [
				{
					down: [{ actionId: 'overlay', options: { input: i.id } }],
					up: [{ actionId: 'overlay_release', options: { input: i.id } }],
				},
			],
			feedbacks: lit('on_overlay', { input: i.id }, BLACK, LIME),
		}
		// (14 Nov: press once for on, again for off; lit lime while it's on)
		presets[`overlay_toggle_${i.id}`] = {
			type: 'button',
			category: 'Inputs to the Overlay (toggle)',
			name: `${i.name}: on / off on the Overlay`,
			style: style(i.name, WHITE, BLACK),
			steps: press('overlay_toggle', { input: i.id }),
			feedbacks: lit('on_overlay', { input: i.id }, BLACK, LIME),
		}
	}
	for (const show of s.shows || []) {
		presets[`show_${show.id}`] = {
			type: 'button',
			category: 'Shows',
			name: `Load ${show.name}`,
			style: style(show.name, WHITE, BLACK),
			steps: press('show', { show: show.id }),
			feedbacks: lit('show_loaded', { show: show.id }, BLACK, SKY),
		}
	}
	for (const [id, text, how, all] of [
		['cut', 'Cut to black', 'cut', false],
		['fade', 'Fade to black', 'fade', false],
		['cut_all', 'Cut everything', 'cut', true],
		['fade_all', 'Fade everything', 'fade', true],
	]) {
		presets[`black_${id}`] = {
			type: 'button',
			category: 'Black',
			name: all ? `${text} (the Festoon and Key lights too)` : text,
			style: style(text, RED, BLACK),
			steps: press('black', { how, all }),
			feedbacks: lit('black', {}, WHITE, RED),
		}
	}
	presets.console_release = {
		type: 'button',
		category: 'Console',
		name: 'Release all',
		style: style(`Release\\n$(${label}:console_held)`, AMBER, BLACK),
		steps: press('console_release', {}),
		feedbacks: lit('console_holds', {}, BLACK, AMBER),
	}
	presets.console_tap = {
		type: 'button',
		category: 'Console',
		name: 'Tap',
		style: style('Tap', WHITE, BLACK),
		steps: press('console_tap', {}),
		feedbacks: [],
	}
	presets.console_next = {
		type: 'button',
		category: 'Console',
		name: 'Next step',
		style: style('Next\\nstep', WHITE, BLACK),
		steps: press('console_next', {}),
		feedbacks: [],
	}
	presets.automations_pause = {
		type: 'button',
		category: 'Automations',
		name: 'Pause / resume',
		style: style('Pause\\nautos', WHITE, BLACK),
		steps: press('automations_pause', { state: 'toggle' }),
		feedbacks: lit('paused', {}, BLACK, AMBER),
	}
	return presets
}

module.exports = {
	DEFAULT_PORT,
	address,
	connectionUrl,
	askUrl,
	path,
	listsKey,
	actionDefinitions,
	feedbackDefinitions,
	variableDefinitions,
	variableValues,
	presetDefinitions,
	variableMatches,
	copiesOf,
}
