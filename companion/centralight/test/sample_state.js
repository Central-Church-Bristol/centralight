// A state as CentraLight's live connection sends it (a small show), for the tests.
'use strict'

module.exports = function sampleState() {
	return {
		show: 'Central Church',
		show_id: 'central-church',
		no_show: false,
		notes: '',
		shows: [
			{ id: 'central-church', name: 'Central Church', loaded: true, notes: '' },
			{ id: 'evening', name: 'Evening', loaded: false, notes: '' },
		],
		inputs: [
			{ id: 'warm-white', name: 'Warm white' },
			{ id: 'worship-blue', name: 'Worship blue' },
		],
		globals: [
			{ name: 'Pulse speed', type: 'number', min: 0, max: 2, dashboard: true },
			{ name: 'accent', type: 'colour', min: 0, max: 1, dashboard: true },
			{ name: 'party', type: 'switch', min: 0, max: 1, dashboard: true },
		],
		house: [
			{ id: 'dimmer-4', name: 'Festoon', slug: 'festoon' },
			{ id: 'dimmer-3', name: 'Key lights', slug: 'key-lights' },
		],
		base: { id: 'warm-white', name: 'Warm white' },
		overlay: {
			on: true,
			opacity: 1,
			copies: [
				{ copy: 1, id: 'worship-blue', name: 'Worship blue' },
				{ copy: 2, id: 'worship-blue', name: 'Worship blue' },
			],
		},
		master: 0.8,
		colour_black: false,
		levels: { 'dimmer-4': { level: 0.4, on: true }, 'dimmer-3': { level: 0.7, on: false } },
		variables: { 'Pulse speed': 0.5, accent: 'FF0000', party: 0 }, // (a switch comes as 1 or 0)
		identify: false,
		output: 'Simulated',
		countdown: null,
		console: { held: 0, lights: [] },
		automations: [{ id: 'end-of-service', name: 'End of service', enabled: true }],
		global_automations: [{ id: 'sunday-morning', name: 'Sunday morning', enabled: true }],
		paused: false,
		next: 'Sun 09:30 Sunday morning',
		lights: [
			{ id: 'slimpar-101', name: 'Front left' },
			{ id: 'slimpar-102', name: 'Front right' },
		],
	}
}
