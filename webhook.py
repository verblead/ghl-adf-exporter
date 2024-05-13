from main import app  # Import your Flask app from main.py

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)  # Run the Flask app in production mode (debug=False)
