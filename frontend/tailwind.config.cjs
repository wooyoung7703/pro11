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
          primary: '#0d6efd',
          accent: '#10b981',
          danger: '#ef4444'
        }
      }
    }
  },
  plugins: [],
};
