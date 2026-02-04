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
 * Handle incoming messages
 * @param {Client} client - WhatsApp client instance
 * @param {Message} message - Incoming message object
 */
async function handleMessage(client, message) {
    const chat = await message.getChat();
    const body = message.body.trim();

    // Log incoming message
    console.log(`[${new Date().toISOString()}] Message from ${message.from}: ${body}`);

    // Check if message is a command
    if (body.startsWith(COMMAND_PREFIX)) {
        await handleCommand(client, message, body);
        return;
    }

    // Handle regular messages with auto-replies
    await handleAutoReply(client, message, body);
}

/**
 * Handle bot commands
 * @param {Client} client - WhatsApp client instance
 * @param {Message} message - Incoming message object
 * @param {string} body - Message body
 */
async function handleCommand(client, message, body) {
    const args = body.slice(COMMAND_PREFIX.length).trim().split(/\s+/);
    const command = args.shift().toLowerCase();

    switch (command) {
        case 'help':
            await sendHelpMessage(message);
            break;

        case 'ping':
            await message.reply('Pong! Bot is online and running.');
            break;

        case 'info':
            await sendInfoMessage(message);
            break;

        case 'echo':
            const echoMessage = args.join(' ');
            if (echoMessage) {
                await message.reply(echoMessage);
            } else {
                await message.reply('Please provide a message to echo. Usage: !echo <message>');
            }
            break;

        case 'time':
            const now = new Date();
            await message.reply(`Current server time: ${now.toLocaleString()}`);
            break;

        case 'joke':
            const randomJoke = jokes[Math.floor(Math.random() * jokes.length)];
            await message.reply(randomJoke);
            break;

        default:
            await message.reply(`Unknown command: ${command}\nType !help to see available commands.`);
    }
}

/**
 * Send help message with available commands
 * @param {Message} message - Incoming message object
 */
async function sendHelpMessage(message) {
    let helpText = '*WhatsApp Bot Commands*\n\n';

    for (const [name, cmd] of Object.entries(commands)) {
        helpText += `*${cmd.usage}*\n${cmd.description}\n\n`;
    }

    helpText += '_Send any message to get an auto-reply!_';

    await message.reply(helpText);
}

/**
 * Send bot info message
 * @param {Message} message - Incoming message object
 */
async function sendInfoMessage(message) {
    const infoText = `*WhatsApp Chatbot*\n\n` +
        `Version: 1.0.0\n` +
        `Status: Online\n` +
        `Uptime: ${formatUptime(process.uptime())}\n\n` +
        `_Type !help to see available commands_`;

    await message.reply(infoText);
}

/**
 * Handle auto-replies for non-command messages
 * @param {Client} client - WhatsApp client instance
 * @param {Message} message - Incoming message object
 * @param {string} body - Message body
 */
async function handleAutoReply(client, message, body) {
    const lowerBody = body.toLowerCase();

    // Greeting responses
    if (containsAny(lowerBody, ['hello', 'hi', 'hey', 'hola', 'greetings'])) {
        await message.reply('Hello! Welcome to the chatbot. Type !help to see what I can do.');
        return;
    }

    // Thank you responses
    if (containsAny(lowerBody, ['thank', 'thanks', 'thx'])) {
        await message.reply("You're welcome! Is there anything else I can help you with?");
        return;
    }

    // Goodbye responses
    if (containsAny(lowerBody, ['bye', 'goodbye', 'see you', 'later'])) {
        await message.reply('Goodbye! Have a great day!');
        return;
    }

    // How are you responses
    if (containsAny(lowerBody, ['how are you', 'how r u', "how's it going"])) {
        await message.reply("I'm doing great, thanks for asking! How can I help you today?");
        return;
    }

    // Default response for unrecognized messages
    // Comment out the line below if you don't want the bot to reply to every message
    // await message.reply('I received your message. Type !help to see available commands.');
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
