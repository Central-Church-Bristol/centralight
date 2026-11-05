// A stand-in for @companion-module/base in the tests: it records what the module tells Companion.
'use strict'

const InstanceStatus = {
	Ok: 'ok',
	Connecting: 'connecting',
	Disconnected: 'disconnected',
	ConnectionFailure: 'connection_failure',
	BadConfig: 'bad_config',
	UnknownError: 'unknown_error',
	UnknownWarning: 'unknown_warning',
	AuthenticationFailure: 'authentication_failure',
}

class InstanceBase {
	constructor() {
		this.label = 'centralight'
		this.statuses = []
		this.logs = []
		this.variables = {}
		this.defined = 0
		this.checked = 0
	}

	setActionDefinitions(actions) {
		this.actions = actions
		this.defined += 1
	}

	setFeedbackDefinitions(feedbacks) {
		this.feedbacks = feedbacks
	}

	setVariableDefinitions(definitions) {
		this.variableDefinitions = definitions
	}

	setVariableValues(values) {
		Object.assign(this.variables, values)
	}

	setPresetDefinitions(presets) {
		this.presets = presets
	}

	checkFeedbacks() {
		this.checked += 1
	}

	updateStatus(status, message) {
		this.statuses.push([status, message])
	}

	log(level, message) {
		this.logs.push([level, message])
	}
}

function combineRgb(r, g, b) {
	return ((r & 0xff) << 16) | ((g & 0xff) << 8) | (b & 0xff)
}

const entry = {}

function runEntrypoint(cls, upgrades) {
	entry.cls = cls
	entry.upgrades = upgrades
}

module.exports = { InstanceBase, InstanceStatus, combineRgb, runEntrypoint, entry }
