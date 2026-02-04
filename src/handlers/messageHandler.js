/**
 * Message Handler Module
 * Processes incoming WhatsApp messages and generates appropriate responses
 */

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
    info: {
        description: 'Get information about the bot',
        usage: '!info'
    },
    echo: {
        description: 'Echo back your message',
        usage: '!echo <message>'
    },
    time: {
        description: 'Get current server time',
        usage: '!time'
    },
    joke: {
        description: 'Get a random joke',
        usage: '!joke'
    }
};

// Sample jokes for the joke command
const jokes = [
    "Why do programmers prefer dark mode? Because light attracts bugs!",
    "Why did the developer go broke? Because he used up all his cache!",
    "A SQL query walks into a bar, walks up to two tables and asks, 'Can I join you?'",
    "Why do Java developers wear glasses? Because they can't C#!",
    "There are only 10 types of people in the world: those who understand binary and those who don't.",
    "Why was the JavaScript developer sad? Because he didn't Node how to Express himself!",
    "What's a programmer's favorite hangout place? Foo Bar!",
    "Why did the programmer quit his job? Because he didn't get arrays!"
];

/**
 * Send a message via Green-API
 * @param {Object} restAPI - Green-API REST client
 * @param {string} chatId - Chat ID to send message to
 * @param {string} message - Message text
 */
async function sendMessage(restAPI, chatId, message) {
    try {
        await restAPI.message.sendMessage(chatId, null, message);
    } catch (error) {
        console.error('Error sending message:', error);
    }
}

/**
 * Handle incoming messages
 * @param {Object} restAPI - Green-API REST client
 * @param {string} chatId - Chat ID
 * @param {string} body - Message body
 * @param {string} senderName - Sender's name
 */
async function handleMessage(restAPI, chatId, body, senderName) {
    const trimmedBody = body.trim();

    // Check if message is a command
    if (trimmedBody.startsWith(COMMAND_PREFIX)) {
        await handleCommand(restAPI, chatId, trimmedBody);
        return;
    }

    // Handle regular messages with auto-replies
    await handleAutoReply(restAPI, chatId, trimmedBody, senderName);
}

/**
 * Handle bot commands
 * @param {Object} restAPI - Green-API REST client
 * @param {string} chatId - Chat ID
 * @param {string} body - Message body
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

        case 'info':
            await sendInfoMessage(restAPI, chatId);
            break;

        case 'echo':
            const echoMessage = args.join(' ');
            if (echoMessage) {
                await sendMessage(restAPI, chatId, echoMessage);
            } else {
                await sendMessage(restAPI, chatId, 'Please provide a message to echo. Usage: !echo <message>');
            }
            break;

        case 'time':
            const now = new Date();
            await sendMessage(restAPI, chatId, `Current server time: ${now.toLocaleString()}`);
            break;

        case 'joke':
            const randomJoke = jokes[Math.floor(Math.random() * jokes.length)];
            await sendMessage(restAPI, chatId, randomJoke);
            break;

        default:
            await sendMessage(restAPI, chatId, `Unknown command: ${command}\nType !help to see available commands.`);
    }
}

/**
 * Send help message with available commands
 * @param {Object} restAPI - Green-API REST client
 * @param {string} chatId - Chat ID
 */
async function sendHelpMessage(restAPI, chatId) {
    let helpText = '*WhatsApp Bot Commands*\n\n';

    for (const [name, cmd] of Object.entries(commands)) {
        helpText += `*${cmd.usage}*\n${cmd.description}\n\n`;
    }

    helpText += '_Send any message to get an auto-reply!_';

    await sendMessage(restAPI, chatId, helpText);
}

/**
 * Send bot info message
 * @param {Object} restAPI - Green-API REST client
 * @param {string} chatId - Chat ID
 */
async function sendInfoMessage(restAPI, chatId) {
    const infoText = `*WhatsApp Chatbot*\n\n` +
        `Version: 1.0.0\n` +
        `Platform: Green-API\n` +
        `Status: Online\n` +
        `Uptime: ${formatUptime(process.uptime())}\n\n` +
        `_Type !help to see available commands_`;

    await sendMessage(restAPI, chatId, infoText);
}

/**
 * Handle auto-replies for non-command messages
 * @param {Object} restAPI - Green-API REST client
 * @param {string} chatId - Chat ID
 * @param {string} body - Message body
 * @param {string} senderName - Sender's name
 */
async function handleAutoReply(restAPI, chatId, body, senderName) {
    const lowerBody = body.toLowerCase();

    // Greeting responses
    if (containsAny(lowerBody, ['hello', 'hi', 'hey', 'hola', 'greetings'])) {
        await sendMessage(restAPI, chatId, `Hello ${senderName}! Welcome to the chatbot. Type !help to see what I can do.`);
        return;
    }

    // Thank you responses
    if (containsAny(lowerBody, ['thank', 'thanks', 'thx'])) {
        await sendMessage(restAPI, chatId, "You're welcome! Is there anything else I can help you with?");
        return;
    }

    // Goodbye responses
    if (containsAny(lowerBody, ['bye', 'goodbye', 'see you', 'later'])) {
        await sendMessage(restAPI, chatId, 'Goodbye! Have a great day!');
        return;
    }

    // How are you responses
    if (containsAny(lowerBody, ['how are you', 'how r u', "how's it going"])) {
        await sendMessage(restAPI, chatId, "I'm doing great, thanks for asking! How can I help you today?");
        return;
    }

    // Default response for unrecognized messages
    // Comment out the line below if you don't want the bot to reply to every message
    // await sendMessage(restAPI, chatId, 'I received your message. Type !help to see available commands.');
}

/**
 * Check if text contains any of the specified keywords
 * @param {string} text - Text to check
 * @param {string[]} keywords - Keywords to look for
 * @returns {boolean}
 */
function containsAny(text, keywords) {
    return keywords.some(keyword => text.includes(keyword));
}

/**
 * Format uptime in human-readable format
 * @param {number} seconds - Uptime in seconds
 * @returns {string}
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
