// CentraLight for Companion: the module. It keeps a live connection to CentraLight (its /api/ws WebSocket), reconnects
// by itself, and shows in Companion whether it's connected, and if not, why. Actions, feedbacks, variables and presets
// are in lib.js.

'use strict'

const { InstanceBase, InstanceStatus, runEntrypoint, combineRgb } = require('@companion-module/base')
const lib = require('./lib')

const RETRY_SECONDS = [1, 2, 5, 10] // waits between tries to reconnect (the last one repeats)
const ASK_SECONDS = 3 // how long the question "why not?" may take (see explain)
const OPEN_SECONDS = 5 // a connection that hasn't opened by then is given up (and tried again)

class CentraLightInstance extends InstanceBase {
	async init(config) {
		this.config = config
		this.state = {}
		this.listsKey = null
		this.socket = null
		this.connected = false
		this.attempt = 0
		this.retries = 0
		this.retryTimer = null
		this.nextId = 1
		this.lastProblem = null
		this.define()
		this.connect()
	}

	async destroy() {
		this.stopped = true
		this.disconnect()
	}

	async configUpdated(config) {
		this.config = config
		this.disconnect()
		this.forget() // (the old CentraLight's state mustn't look live while the new one is tried)
		this.retries = 0
		this.listsKey = null // (a renamed connection: the presets name it in their variables)
		this.define()
		this.connect()
	}

	getConfigFields() {
		return [
			{
				type: 'static-text',
				id: 'about',
				label: 'CentraLight',
				value:
					'The PC CentraLight runs on: 127.0.0.1 when it is this PC. The port and the key are in CentraLight, ' +
					'Settings → Remote. The key is only needed from another computer (with "Allow other computers" on).',
				width: 12,
			},
			{ type: 'textinput', id: 'host', label: 'CentraLight PC', default: '127.0.0.1', width: 6 },
			{ type: 'number', id: 'port', label: 'Port', default: 8746, min: 1, max: 65535, width: 3 },
			{ type: 'textinput', id: 'key', label: 'Key (from another computer)', default: '', width: 9 },
		]
	}

	// ------------------------------------------------------------ the connection

	connect() {
		if (this.stopped) return
		let url
		try {
			url = lib.connectionUrl(this.config)
		} catch (error) {
			this.updateStatus(InstanceStatus.BadConfig, error.message) // (no point trying again until it's changed)
			return
		}
		this.updateStatus(InstanceStatus.Connecting)
		let socket
		try {
			socket = new WebSocket(url)
		} catch (error) {
			this.updateStatus(InstanceStatus.BadConfig, error.message)
			return
		}
		const attempt = ++this.attempt
		let opened = false
		this.socket = socket
		const opening = setTimeout(() => {
			if (this.socket === socket && !opened) {
				try {
					socket.close()
				} catch (error) {
					// (it may be closing already)
				}
				gone()
			}
		}, OPEN_SECONDS * 1000)
		socket.addEventListener('open', () => {
			clearTimeout(opening)
			if (this.socket !== socket) return
			opened = true
			this.connected = true
			this.lastProblem = null
			this.updateStatus(InstanceStatus.Ok)
		})
		socket.addEventListener('message', (event) => {
			if (this.socket === socket) this.received(event.data)
		})
		// (gone: once, whichever comes first; a connection that never opened sends only "error", no "close")
		const gone = () => {
			clearTimeout(opening)
			if (this.socket !== socket) return // (an old connection, after a settings change)
			this.socket = null
			this.connected = false
			this.forget()
			if (opened) {
				this.updateStatus(InstanceStatus.Disconnected, 'CentraLight closed the connection (was it closed?)')
			} else {
				this.explain(attempt)
			}
			this.retryLater()
		}
		socket.addEventListener('close', gone)
		socket.addEventListener('error', (event) => {
			if (this.socket !== socket) return
			const why = (event && (event.message || (event.error && event.error.message))) || 'no answer'
			if (why !== this.lastProblem) {
				this.lastProblem = why // (once each new reason, not on every retry)
				this.log('debug', `CentraLight: ${why}`)
			}
			if (!opened) gone()
		})
	}

	// What CentraLight showed is gone with the connection: keys go dark, every variable empties (not left as it was,
	// which would look live).
	forget() {
		const empty = Object.fromEntries(lib.variableDefinitions(this.state).map((v) => [v.variableId, '']))
		this.state = {}
		this.setVariableValues({ ...empty, ...lib.variableValues({}) })
		this.checkFeedbacks()
	}

	// A connection that never opened: ask why, at the same address as a plain web page (its answer has the reason).
	async explain(attempt) {
		let status = 0
		try {
			const reply = await fetch(lib.askUrl(this.config), { signal: AbortSignal.timeout(ASK_SECONDS * 1000) })
			status = reply.status
			await reply.body?.cancel()
		} catch (error) {
			status = 0
		}
		if (this.stopped || this.connected || attempt !== this.attempt) return // (meanwhile it connected, or was changed)
		if (status === 403) {
			this.updateStatus(InstanceStatus.AuthenticationFailure,
				'CentraLight refused: check the key, and "Allow other computers" in its Settings → Remote')
		} else if (status === 503) {
			this.updateStatus(InstanceStatus.ConnectionFailure, 'CentraLight has too many live connections already')
		} else if (status === 404) {
			this.updateStatus(InstanceStatus.ConnectionFailure,
				'This CentraLight has no live connection yet: it needs updating to the version with this module')
		} else if (status === 426) {
			this.updateStatus(InstanceStatus.ConnectionFailure, 'CentraLight answered, but the connection didn\'t open')
		} else if (status) {
			this.updateStatus(InstanceStatus.ConnectionFailure, `CentraLight answered ${status}`)
		} else {
			this.updateStatus(InstanceStatus.Disconnected, 'CentraLight is not answering (is it running?)')
		}
	}

	disconnect() {
		clearTimeout(this.retryTimer)
		this.retryTimer = null
		this.attempt += 1 // (an answer still on its way about the old connection is ignored)
		const socket = this.socket
		this.socket = null
		this.connected = false
		if (socket) {
			try {
				socket.close()
			} catch (error) {
				// (it may be closed already)
			}
		}
	}

	retryLater() {
		if (this.stopped || this.retryTimer) return
		const wait = RETRY_SECONDS[Math.min(this.retries, RETRY_SECONDS.length - 1)]
		this.retries += 1
		this.retryTimer = setTimeout(() => {
			this.retryTimer = null
			this.connect()
		}, wait * 1000)
	}

	received(text) {
		let message
		try {
			message = JSON.parse(String(text))
		} catch (error) {
			return
		}
		if (message.type === 'state') {
			this.retries = 0 // (only once CentraLight has really answered: a connection that keeps failing backs off)
			this.state = message.state || {}
			const key = lib.listsKey(this.state)
			if (key !== this.listsKey) {
				this.listsKey = key
				this.define()
			}
			this.setVariableValues(lib.variableValues(this.state))
			this.checkFeedbacks()
		} else if (message.type === 'result' && message.ok === false) {
			this.log('warn', `CentraLight: ${message.error || 'that did not work'}`)
		}
	}

	send(path, params) {
		const socket = this.socket
		if (!socket || socket.readyState !== WebSocket.OPEN) {
			this.log('warn', 'CentraLight is not connected, so that button did nothing')
			return
		}
		socket.send(JSON.stringify({ type: 'command', id: this.nextId++, command: { path, params } }))
	}

	// ------------------------------------------------------------ what Companion shows

	define() {
		const getState = () => this.state
		this.setActionDefinitions(lib.actionDefinitions(this.state, (path, params) => this.send(path, params), getState))
		this.setFeedbackDefinitions(lib.feedbackDefinitions(getState, combineRgb, () => this.connected))
		this.setVariableDefinitions(lib.variableDefinitions(this.state))
		this.setVariableValues(lib.variableValues(this.state))
		this.setPresetDefinitions(lib.presetDefinitions(this.state, combineRgb, this.label || 'centralight'))
	}
}

runEntrypoint(CentraLightInstance, [])
