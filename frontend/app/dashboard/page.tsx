<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Attack Simulation Dashboard</title>
    <link rel="stylesheet" href="/_next/static/css/globals.css" />
  </head>
  <body>
    <div className="min-h-screen bg-gray-100 p-8">
      <h1 className="text-2xl font-bold mb-6">Attack Simulation Dashboard</h1>
      <p className="text-gray-600 mb-6">Select attack test categories:</p>
      <div className="space-y-2">
        <div className="flex items-start space-x-2">
          <input
            type="checkbox"
            id="phishing"
            className="w-4 h-4 rounded border-gray-500 focus:ring-2 focus:ring-blue-500 cursor-pointer"
            defaultChecked />
          <label htmlFor="phishing" className="ml-2 text-gray-700 cursor-pointer">
            Phishing Simulation
          </label>
        </div>
        <div className="flex items-start space-x-2">
          <input
            type="checkbox"
            id="malware"
            className="w-4 h-4 rounded border-gray-500 focus:ring-2 focus:ring-blue-500 cursor-pointer"
            defaultChecked />
          <label htmlFor="malware" className="ml-2 text-gray-700 cursor-pointer">
            Malware Analysis
          </label>
        </div>
        <div className="flex items-start space-x-2">
          <input
            type="checkbox"
            id="bruteforce"
            className="w-4 h-4 rounded border-gray-500 focus:ring-2 focus:ring-blue-500 cursor-pointer"
            defaultChecked />
          <label htmlFor="bruteforce" className="ml-2 text-gray-700 cursor-pointer">
            Brute Force Attack
          </label>
        </div>
      </div>
      <button
        onClick="alert('Running selected tests...')"
        className="mt-4 py-2 px-6 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors">
        Run Selected Tests
      </button>
    </div>
  </body>
</html>