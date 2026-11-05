// A pretend CentraLight for the module's tests: just enough of a WebSocket server (Node has none of its own) to send a
// state and record the commands that come back.
'use strict'

const http = require('node:http')
const crypto = require('node:crypto')

const GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

function encode(text) {
	const payload = Buffer.from(text, 'utf8')
	let head
	if (payload.length < 126) {
		head = Buffer.from([0x81, payload.length])
	} else if (payload.length < 65536) {
		head = Buffer.alloc(4)
		head[0] = 0x81
		head[1] = 126
		head.writeUInt16BE(payload.length, 2)
	} else {
		head = Buffer.alloc(10)
		head[0] = 0x81
		head[1] = 127
		head.writeBigUInt64BE(BigInt(payload.length), 2)
	}
	return Buffer.concat([head, payload])
}

// The client's frames (masked, as clients must send them): the text messages, and whether it asked to close.
function decode(buffer) {
	const messages = []
	let offset = 0
	let closing = false
	while (buffer.length - offset >= 2) {
		const opcode = buffer[offset] & 0x0f
		let size = buffer[offset + 1] & 0x7f
		let at = offset + 2
		if (size === 126) {
			if (buffer.length < at + 2) break
			size = buffer.readUInt16BE(at)
			at += 2
		} else if (size === 127) {
			if (buffer.length < at + 8) break
			size = Number(buffer.readBigUInt64BE(at))
			at += 8
		}
		if (buffer.length < at + 4 + size) break
		const mask = buffer.subarray(at, at + 4)
		const data = Buffer.from(buffer.subarray(at + 4, at + 4 + size))
		for (let i = 0; i < data.length; i++) data[i] ^= mask[i % 4]
		if (opcode === 0x1) messages.push(data.toString('utf8'))
		if (opcode === 0x8) closing = true
		offset = at + 4 + size
	}
	return { messages, closing, rest: buffer.subarray(offset) }
}

async function startFake(state) {
	// refuse: an HTTP status to answer everything with instead (403: a wrong key; 503: too many connections)
	const fake = { state, commands: [], urls: [], clients: new Set(), answer: { ok: true }, refuse: 0 }
	const server = http.createServer((request, response) => {
		// (as CentraLight: a plain GET of the live connection's address answers why it can't be one)
		response.writeHead(request.url.startsWith('/api/ws') ? fake.refuse || 426 : 200)
		response.end()
	})
	server.on('upgrade', (request, socket) => {
		fake.urls.push(request.url)
		if (fake.refuse) {
			socket.end(`HTTP/1.1 ${fake.refuse} Refused\r\nContent-Length: 0\r\nConnection: close\r\n\r\n`)
			return
		}
		const accept = crypto.createHash('sha1').update(request.headers['sec-websocket-key'] + GUID).digest('base64')
		socket.write(
			'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n' +
				`Sec-WebSocket-Accept: ${accept}\r\n\r\n`
		)
		fake.clients.add(socket)
		let buffer = Buffer.alloc(0)
		socket.on('data', (chunk) => {
			const read = decode(Buffer.concat([buffer, chunk]))
			buffer = read.rest
			for (const text of read.messages) {
				const message = JSON.parse(text)
				if (message.type === 'command') {
					fake.commands.push(message.command)
					socket.write(encode(JSON.stringify({ type: 'result', id: message.id, ...fake.answer })))
				}
			}
			if (read.closing) socket.end(Buffer.from([0x88, 0x02, 0x03, 0xe8]))
		})
		socket.on('close', () => fake.clients.delete(socket))
		socket.on('error', () => fake.clients.delete(socket))
		socket.write(encode(JSON.stringify({ type: 'state', state: fake.state })))
	})
	await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
	fake.port = server.address().port
	fake.push = (state) => {
		fake.state = state
		for (const socket of fake.clients) socket.write(encode(JSON.stringify({ type: 'state', state })))
	}
	fake.drop = () => {
		for (const socket of fake.clients) socket.destroy()
		fake.clients.clear()
	}
	fake.stop = () =>
		new Promise((resolve) => {
			fake.drop()
			server.close(() => resolve())
		})
	return fake
}

module.exports = { startFake }
