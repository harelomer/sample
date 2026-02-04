# WhatsApp Chatbot

A simple WhatsApp chatbot built with Node.js and whatsapp-web.js.

## Features

- QR code authentication
- Auto-replies for common greetings
- Bot commands with `!` prefix
- Session persistence (no need to scan QR code every time)
- Graceful shutdown handling

## Available Commands

| Command | Description |
|---------|-------------|
| `!help` | Show available commands |
| `!ping` | Check if bot is online |
| `!info` | Get information about the bot |
| `!echo <message>` | Echo back your message |
| `!time` | Get current server time |
| `!joke` | Get a random programming joke |

## Prerequisites

- Node.js 16 or higher
- npm or yarn
- A WhatsApp account

## Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd whatsapp-chatbot
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Copy the environment file:
   ```bash
   cp .env.example .env
   ```

4. Start the bot:
   ```bash
   npm start
   ```

5. Scan the QR code with WhatsApp:
   - Open WhatsApp on your phone
   - Go to Settings > Linked Devices
   - Tap "Link a Device"
   - Scan the QR code shown in the terminal

## Development

Run with auto-reload on file changes:

```bash
npm run dev
```

## Project Structure

```
whatsapp-chatbot/
├── src/
│   ├── index.js              # Main entry point
│   └── handlers/
│       └── messageHandler.js # Message processing logic
├── .env.example              # Environment variables template
├── .gitignore               # Git ignore rules
├── package.json             # Project configuration
└── README.md                # This file
```

## Customization

### Adding New Commands

Edit `src/handlers/messageHandler.js`:

1. Add the command to the `commands` object:
   ```javascript
   const commands = {
       // ... existing commands
       mycommand: {
           description: 'My custom command',
           usage: '!mycommand'
       }
   };
   ```

2. Add a case in the `handleCommand` switch statement:
   ```javascript
   case 'mycommand':
       await message.reply('This is my custom command response!');
       break;
   ```

### Modifying Auto-Replies

Edit the `handleAutoReply` function in `src/handlers/messageHandler.js` to add or modify automatic responses.

## Notes

- The bot uses `LocalAuth` strategy to persist session data in `.wwebjs_auth/` directory
- First-time authentication requires scanning a QR code
- Subsequent runs will use the saved session (no QR scan needed)
- Make sure to keep your session data secure and never commit it to version control

## Troubleshooting

### QR Code Not Showing
- Make sure your terminal supports displaying characters properly
- Try running in a different terminal emulator

### Authentication Failed
- Delete the `.wwebjs_auth/` directory and try again
- Make sure WhatsApp Web isn't already connected on another device

### Bot Not Responding
- Check the console for error messages
- Ensure the bot is showing as "ready" in the logs
- Verify you're sending messages to the correct WhatsApp number

## License

MIT
