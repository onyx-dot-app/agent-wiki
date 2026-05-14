/** @type {import('tailwindcss').Config} */
module.exports = {
  presets: [require("@onyx-ai/opal/tailwind-preset")],
  content: [
    "./src/**/*.{ts,tsx}",
    "./node_modules/@onyx-ai/opal/dist/**/*.{js,mjs}",
  ],
};
