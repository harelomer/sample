/**
 * Message Handler Module
 * Processes incoming WhatsApp messages using Claude AI
 */

const Anthropic = require('@anthropic-ai/sdk');

// Initialize Anthropic client
const anthropic = new Anthropic();

// Store conversation history per chat (in-memory, resets on restart)
const conversationHistory = new Map();
const MAX_HISTORY = 20; // Keep last 20 messages per chat

// Command prefix for bot commands
const COMMAND_PREFIX = '!';

// Available commands
const commands = {
    help: {
        description: 'Show available commands',
        usage: '!help'
    },
    ping: {
        description: 'Check if bot is online',
        usage: '!ping'
    },
    clear: {
        description: 'Clear conversation history',
        usage: '!clear'
    },
    info: {
        description: 'Get information about the bot',
        usage: '!info'
    }
};

/**
 * Send a message via Green-API
 */
async function sendMessage(restAPI, chatId, message) {
    try {
        await restAPI.message.sendMessage(chatId, null, message);
    } catch (error) {
        console.error('Error sending message:', error);
    }
}

/**
 * Get AI response from Claude
 */
async function getAIResponse(chatId, userMessage, senderName) {
    // Get or create conversation history for this chat
    if (!conversationHistory.has(chatId)) {
        conversationHistory.set(chatId, []);
    }
    const history = conversationHistory.get(chatId);

    // Add user message to history
    history.push({
        role: 'user',
        content: userMessage
    });

    // Trim history if too long
    while (history.length > MAX_HISTORY) {
        history.shift();
    }

    try {
        const response = await anthropic.messages.create({
            model: 'claude-sonnet-4-20250514',
            max_tokens: 1024,
            system: `You are a helpful WhatsApp assistant. Keep responses concise and friendly, suitable for chat messages. The user's name is ${senderName}. Use plain text formatting (no markdown) as WhatsApp has limited formatting support. Keep responses brief - ideally under 200 words.`,
            messages: history
        });

        const assistantMessage = response.content[0].text;

        // Add assistant response to history
        history.push({
            role: 'assistant',
            content: assistantMessage
        });

        return assistantMessage;
    } catch (error) {
        console.error('Error getting AI response:', error);
        return "Sorry, I'm having trouble processing your request right now. Please try again.";
    }
}

/**
 * Handle incoming messages
 */
async function handleMessage(restAPI, chatId, body, senderName) {
    const trimmedBody = body.trim();

    // Check if message is a command
    if (trimmedBody.startsWith(COMMAND_PREFIX)) {
        await handleCommand(restAPI, chatId, trimmedBody);
        return;
    }

    // Get AI response for all other messages
    console.log(`[AI] Getting response for: ${trimmedBody}`);
    const aiResponse = await getAIResponse(chatId, trimmedBody, senderName);
    console.log(`[AI] Response: ${aiResponse.substring(0, 100)}...`);
    await sendMessage(restAPI, chatId, aiResponse);
}

/**
 * Handle bot commands
 */
async function handleCommand(restAPI, chatId, body) {
    const args = body.slice(COMMAND_PREFIX.length).trim().split(/\s+/);
    const command = args.shift().toLowerCase();

    switch (command) {
        case 'help':
            await sendHelpMessage(restAPI, chatId);
            break;

        case 'ping':
            await sendMessage(restAPI, chatId, 'Pong! Bot is online and running.');
            break;

        case 'clear':
            conversationHistory.delete(chatId);
            await sendMessage(restAPI, chatId, 'Conversation history cleared. Starting fresh!');
            break;

        case 'info':
            await sendInfoMessage(restAPI, chatId);
            break;

        default:
            await sendMessage(restAPI, chatId, `Unknown command: ${command}\nType !help to see available commands.`);
    }
}

/**
 * Send help message with available commands
 */
async function sendHelpMessage(restAPI, chatId) {
    let helpText = '*WhatsApp AI Bot*\n\n';
    helpText += 'I can answer any question using AI!\n\n';
    helpText += '*Commands:*\n';

    for (const [name, cmd] of Object.entries(commands)) {
        helpText += `${cmd.usage} - ${cmd.description}\n`;
    }

    helpText += '\nJust send any message and I\'ll respond!';

    await sendMessage(restAPI, chatId, helpText);
}

/**
 * Send bot info message
 */
async function sendInfoMessage(restAPI, chatId) {
    const infoText = `*WhatsApp AI Chatbot*\n\n` +
        `Version: 2.0.0\n` +
        `AI: Claude (Anthropic)\n` +
        `Platform: Green-API\n` +
        `Status: Online\n` +
        `Uptime: ${formatUptime(process.uptime())}`;

    await sendMessage(restAPI, chatId, infoText);
}

/**
 * Format uptime in human-readable format
 */
function formatUptime(seconds) {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);

    const parts = [];
    if (days > 0) parts.push(`${days}d`);
    if (hours > 0) parts.push(`${hours}h`);
    if (minutes > 0) parts.push(`${minutes}m`);
    parts.push(`${secs}s`);

    return parts.join(' ');
}

module.exports = {
    handleMessage,
    commands
};
