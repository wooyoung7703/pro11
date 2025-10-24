/**** Tailwind Config ****/
module.exports = {
  content: [
    './index.html',
    './src/**/*.{vue,ts,js,tsx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        brand: {
          // Unified dark palette
          900: '#0b1018', // app bg
          800: '#111927', // top bar / cards
          700: '#0f1a2a', // panel bg
          600: '#1b2a40', // button/bg subtle
          primary: '#38bdf8', // sky-400 like
          accent: '#10b981',  // emerald-500 like
          danger: '#ef4444',
        },
        // Optional alias for borders
        border: {
          brand: '#1f2a44',
        },
      }
    }
  },
  plugins: [],
};
