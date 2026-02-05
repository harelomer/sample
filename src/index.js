const axios = require('axios');
const { HttpsProxyAgent } = require('https-proxy-agent');
require('dotenv').config();

const { handleMessage } = require('./handlers/messageHandler');

// Green-API credentials from environment variables
const ID_INSTANCE = process.env.GREEN_API_ID_INSTANCE;
const API_TOKEN_INSTANCE = process.env.GREEN_API_TOKEN_INSTANCE;
const API_HOST = process.env.GREEN_API_HOST || 'https://api.green-api.com';

if (!ID_INSTANCE || !API_TOKEN_INSTANCE) {
    console.error('Error: GREEN_API_ID_INSTANCE and GREEN_API_TOKEN_INSTANCE must be set in .env file');
    console.error('Get your credentials at https://green-api.com/');
    process.exit(1);
}

// Configure axios with proxy if available
const axiosConfig = {};
if (process.env.https_proxy) {
    axiosConfig.httpsAgent = new HttpsProxyAgent(process.env.https_proxy);
    axiosConfig.proxy = false;
}

const api = axios.create(axiosConfig);

// Message buffer for debouncing - collects rapid messages before responding
const messageBuffer = new Map(); // chatId -> { messages: [], senderName: string, timer: timeout }
const DEBOUNCE_DELAY = 1000; // Wait 1 second for additional messages
const MAX_BUFFER_SIZE = 1000; // Max concurrent chat buffers to prevent unbounded memory growth

// Build API URL
// NOTE: Green API embeds the token in the URL path, which means it can appear in
// HTTP access logs and proxy logs. This is a limitation of the Green API design.
// Ensure access logs are not exposed publicly and consider log redaction.
function buildUrl(method) {
    return `${API_HOST}/waInstance${ID_INSTANCE}/${method}/${API_TOKEN_INSTANCE}`;
}

// API wrapper object for compatibility with message handler
const restAPI = {
    message: {
        async sendMessage(chatId, idMessage, message) {
            const url = buildUrl('sendMessage');
            const response = await api.post(url, {
                chatId,
                message
            });
            return response.data;
        }
    },
    instance: {
        async getStateInstance() {
            const url = buildUrl('getStateInstance');
            const response = await api.get(url);
            return response.data;
        }
    }
};

// Poll for incoming notifications
async function receiveNotification() {
    const url = buildUrl('receiveNotification');
    const response = await api.get(url);
    return response.data;
}

// Delete notification after processing
async function deleteNotification(receiptId) {
    const url = buildUrl('deleteNotification') + `/${receiptId}`;
    const response = await api.delete(url);
    return response.data;
}

// Start receiving notifications
async function startBot() {
    console.log('Initializing WhatsApp Bot with Green-API...');

    try {
        // Check account status
        const stateInstance = await restAPI.instance.getStateInstance();
        console.log('Instance state:', stateInstance.stateInstance);

        if (stateInstance.stateInstance !== 'authorized') {
            console.log('Instance not authorized. Please scan the QR code at https://console.green-api.com/');
            console.log('After authorization, restart the bot.');
            return;
        }

        console.log('WhatsApp Bot is ready!');
        console.log('Bot is now listening for messages...');

        // Start polling for messages
        pollMessages();

    } catch (error) {
        console.error('Error starting bot:', error.message);
        process.exit(1);
    }
}

// Poll for new messages
async function pollMessages() {
    while (true) {
        try {
            const notification = await receiveNotification();

            if (notification) {
                await processNotification(notification.body);
                await deleteNotification(notification.receiptId);
            }

            // Small delay between polls
            await new Promise(resolve => setTimeout(resolve, 100));
        } catch (error) {
            console.error('Error polling messages:', error.message);
            await new Promise(resolve => setTimeout(resolve, 5000));
        }
    }
}

// Process incoming notifications
async function processNotification(body) {
    const typeWebhook = body.typeWebhook;

    // Only process incoming messages
    if (typeWebhook === 'incomingMessageReceived') {
        const messageData = body.messageData;
        const senderData = body.senderData;

        // Get message text
        let messageText = '';
        if (messageData.typeMessage === 'textMessage') {
            messageText = messageData.textMessageData?.textMessage || '';
        } else if (messageData.typeMessage === 'extendedTextMessage') {
            messageText = messageData.extendedTextMessageData?.text || '';
        }

        if (messageText) {
            const chatId = senderData.chatId;
            const senderName = senderData.senderName || 'Unknown';

            console.log(`[${new Date().toISOString()}] Message from ${senderName} (${chatId}): ${messageText}`);

            // Add message to buffer and debounce
            bufferMessage(chatId, messageText, senderName);
        }
    }
}

// Buffer messages and debounce before processing
function bufferMessage(chatId, messageText, senderName) {
    // Get or create buffer entry for this chat
    let bufferEntry = messageBuffer.get(chatId);

    if (bufferEntry) {
        // Clear existing timer
        clearTimeout(bufferEntry.timer);
        // Add message to buffer
        bufferEntry.messages.push(messageText);
    } else {
        // Evict oldest entry if buffer is at capacity
        if (messageBuffer.size >= MAX_BUFFER_SIZE) {
            const oldestKey = messageBuffer.keys().next().value;
            const oldestEntry = messageBuffer.get(oldestKey);
            if (oldestEntry && oldestEntry.timer) {
                clearTimeout(oldestEntry.timer);
            }
            messageBuffer.delete(oldestKey);
        }
        // Create new buffer entry
        bufferEntry = {
            messages: [messageText],
            senderName: senderName
        };
        messageBuffer.set(chatId, bufferEntry);
    }

    // Set new timer to process messages after delay
    bufferEntry.timer = setTimeout(async () => {
        const entry = messageBuffer.get(chatId);
        if (entry) {
            messageBuffer.delete(chatId);

            // Combine all messages into one
            const combinedMessage = entry.messages.join(' ');
            console.log(`[${new Date().toISOString()}] Processing combined message: ${combinedMessage}`);

            // Handle the combined message
            try {
                await handleMessage(restAPI, chatId, combinedMessage, entry.senderName);
            } catch (error) {
                console.error('Error handling message:', error);
            }
        }
    }, DEBOUNCE_DELAY);
}

// Graceful shutdown
process.on('SIGINT', () => {
    console.log('\nShutting down gracefully...');
    process.exit(0);
});

process.on('SIGTERM', () => {
    console.log('\nShutting down gracefully...');
    process.exit(0);
});

// Start the bot
startBot();
