const whatsAppClient = require('@green-api/whatsapp-api-client');
require('dotenv').config();

const { handleMessage } = require('./handlers/messageHandler');

// Green-API credentials from environment variables
const ID_INSTANCE = process.env.GREEN_API_ID_INSTANCE;
const API_TOKEN_INSTANCE = process.env.GREEN_API_TOKEN_INSTANCE;

if (!ID_INSTANCE || !API_TOKEN_INSTANCE) {
    console.error('Error: GREEN_API_ID_INSTANCE and GREEN_API_TOKEN_INSTANCE must be set in .env file');
    console.error('Get your credentials at https://green-api.com/');
    process.exit(1);
}

// Create REST API client for sending messages
const restAPI = whatsAppClient.restAPI({
    idInstance: ID_INSTANCE,
    apiTokenInstance: API_TOKEN_INSTANCE
});

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

        // Start webhook for receiving messages
        const webhookAPI = whatsAppClient.webhookAPI(restAPI, async (body) => {
            try {
                await processNotification(body);
            } catch (error) {
                console.error('Error processing notification:', error);
            }
        });

        // Start receiving notifications
        await webhookAPI.Start();

    } catch (error) {
        console.error('Error starting bot:', error.message);
        process.exit(1);
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

            // Handle the message
            await handleMessage(restAPI, chatId, messageText, senderName);
        }
    }
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
