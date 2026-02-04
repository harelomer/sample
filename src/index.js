const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
require('dotenv').config();

const { handleMessage } = require('./handlers/messageHandler');

// Initialize WhatsApp client with local authentication
const client = new Client({
    authStrategy: new LocalAuth(),
    puppeteer: {
        headless: true,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--no-first-run',
            '--no-zygote',
            '--disable-gpu'
        ]
    }
});

// Generate QR code for authentication
client.on('qr', (qr) => {
    console.log('Scan the QR code below to authenticate:');
    qrcode.generate(qr, { small: true });
});

// Client is ready
client.on('ready', () => {
    console.log('WhatsApp Bot is ready!');
    console.log('Bot is now listening for messages...');
});

// Handle authentication
client.on('authenticated', () => {
    console.log('Authentication successful!');
});

// Handle authentication failure
client.on('auth_failure', (msg) => {
    console.error('Authentication failed:', msg);
});

// Handle disconnection
client.on('disconnected', (reason) => {
    console.log('Client was disconnected:', reason);
    // Attempt to reconnect
    client.initialize();
});

// Handle incoming messages
client.on('message', async (message) => {
    try {
        await handleMessage(client, message);
    } catch (error) {
        console.error('Error handling message:', error);
    }
});

// Handle message creation (including own messages)
client.on('message_create', async (message) => {
    // Only process messages from others, not from self
    if (message.fromMe) return;
});

// Initialize the client
console.log('Initializing WhatsApp Bot...');
client.initialize();

// Graceful shutdown
process.on('SIGINT', async () => {
    console.log('\nShutting down gracefully...');
    await client.destroy();
    process.exit(0);
});

process.on('SIGTERM', async () => {
    console.log('\nShutting down gracefully...');
    await client.destroy();
    process.exit(0);
});
