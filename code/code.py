import time
import board
import json
import gc
import displayio
import terminalio
import digitalio
import adafruit_minimqtt.adafruit_minimqtt as MQTT
from adafruit_matrixportal.matrixportal import MatrixPortal
import adafruit_esp32spi.adafruit_esp32spi_socket as socket
import adafruit_requests as requests
import microcontroller

# Version for OTA updates
VERSION = "1.0.0"

# Color-based priority levels (higher number = higher priority)
PRIORITY_LEVELS = {
    'red': 5,      # Critical alerts
    'orange': 4,   # Important alerts  
    'yellow': 3,   # Warnings
    'blue': 2,     # Information
    'green': 1     # Status/persistent
}

# Message display durations (seconds)
DISPLAY_DURATIONS = {
    'red': 15,
    'orange': 12,
    'yellow': 10, 
    'blue': 8,
    'green': 0  # Persistent until overridden
}

# Color schemes - default is colored text on black background
COLORS = {
    'red': {
        'normal': {'fg': 0xFF0000, 'bg': 0x000000, 'flash': False},     # Red text/Black bg, no flashing
        'alert': {'fg': 0xFFFFFF, 'bg': 0xFF0000, 'flash': False}       # White text/Red bg, no flashing
    },
    'orange': {
        'normal': {'fg': 0xFF8000, 'bg': 0x000000, 'flash': False},     # Orange text/Black bg
        'alert': {'fg': 0x000000, 'bg': 0xFF8000, 'flash': False}       # Black text/Orange bg
    },
    'yellow': {
        'normal': {'fg': 0xFFFF00, 'bg': 0x000000, 'flash': False},     # Yellow text/Black bg
        'alert': {'fg': 0x000000, 'bg': 0xFFFF00, 'flash': False}       # Black text/Yellow bg
    },
    'blue': {
        'normal': {'fg': 0x0080FF, 'bg': 0x000000, 'flash': False},     # Blue text/Black bg
        'alert': {'fg': 0xFFFFFF, 'bg': 0x0080FF, 'flash': False}       # White text/Blue bg
    },
    'green': {
        'normal': {'fg': 0x00FF00, 'bg': 0x000000, 'flash': False},     # Green text/Black bg (same as before)
        'alert': {'fg': 0x000000, 'bg': 0x00FF00, 'flash': False}       # Black text/Green bg
    }
}

# Animation definitions
ANIMATIONS = {
    'driveway': {'pattern': 'slide_left', 'speed': 0.3, 'repeat': 2},
    'frontdoor': {'pattern': 'flash_border', 'speed': 0.5, 'repeat': 3},
    'build': {'pattern': 'progress_bar', 'speed': 0.2, 'repeat': 1},
    'complete': {'pattern': 'checkmark', 'speed': 0.4, 'repeat': 1},
    'motion': {'pattern': 'radar_sweep', 'speed': 0.3, 'repeat': 2},
    'doorbell': {'pattern': 'pulse', 'speed': 0.6, 'repeat': 4}
}

class MessageQueue:
    def __init__(self):
        self.messages = []
        self.current_message = None
        self.display_start_time = 0
        
    def add_message(self, priority, text, source="unknown", duration=None, animation=None, is_alert=False):
        """Add message to queue, handling priority interruption"""
        priority_level = PRIORITY_LEVELS.get(priority, 1)
        
        if duration is None:
            duration = DISPLAY_DURATIONS.get(priority, 4)
            # For long scrolling messages, calculate duration based on text length
            if len(text) > 15:  # Scrolling threshold
                # Allow enough time for text to scroll completely
                # At 0.4 seconds per character, plus padding for full cycle
                scroll_time = (len(text) + 8) * 0.4  # Extra padding for complete scroll
                duration = max(duration, scroll_time)
            
        message = {
            'priority': priority_level,
            'priority_name': priority,
            'text': text,
            'source': source,
            'duration': duration,
            'animation': animation,
            'is_alert': is_alert,
            'timestamp': time.monotonic()
        }
        
        # If higher priority than current, interrupt immediately
        if (self.current_message is None or 
            priority_level > self.current_message['priority']):
            
            # Add current message back to queue if it was interrupted
            if self.current_message is not None:
                self.messages.append(self.current_message)
                
            self.current_message = message
            self.display_start_time = time.monotonic()
            return True  # Signal immediate display needed
        else:
            # Add to queue in priority order
            inserted = False
            for i, msg in enumerate(self.messages):
                if priority_level > msg['priority']:
                    self.messages.insert(i, message)
                    inserted = True
                    break
            if not inserted:
                self.messages.append(message)
            return False
    
    def get_next_message(self):
        """Get next message from queue"""
        if self.messages:
            return self.messages.pop(0)
        return None
    
    def should_advance(self):
        """Check if current message should be replaced"""
        if self.current_message is None:
            return True
            
        # Green messages persist until overridden
        if self.current_message['priority_name'] == 'green':
            return len(self.messages) > 0
            
        # Other messages have time limits
        elapsed = time.monotonic() - self.display_start_time
        return elapsed >= self.current_message['duration']

class MatrixController:
    def __init__(self):
        self.matrixportal = None
        self.mqtt = None
        self.message_queue = MessageQueue()
        self.last_heartbeat = 0
        self.connection_attempts = 0
        self.flash_state = False
        self.last_flash_time = 0
        self.scroll_offset = 0
        self.last_scroll_time = 0
        self.animation_state = None
        self.animation_frame = 0
        self.animation_start_time = 0
        self.scroll_text = ""
        self.scroll_position = 0
        self.is_scrolling = False
        self.update_server = None
        self.last_update_check = 0
        self.setup_display()
        
    def setup_display(self):
        """Initialize the matrix display"""
        try:
            displayio.release_displays()
            self.matrixportal = MatrixPortal(
                status_neopixel=board.NEOPIXEL, 
                debug=False, 
                bit_depth=6
            )
            
            # Single text display - use only one layer
            self.matrixportal.add_text(
                text_font=terminalio.FONT,
                text_position=(1, 10),  # Position in middle of 32px height
                text_scale=1,
                text_anchor_point=(0, 0),
                scrolling=False
            )
            
            print("Display initialized successfully")
            self.show_status("INIT", "System starting...")
            
        except Exception as e:
            print(f"Display setup failed: {e}")
            
    def setup_network(self):
        """Connect to WiFi with retry logic"""
        try:
            from secrets import secrets
        except ImportError:
            print("ERROR: secrets.py not found!")
            return False
            
        max_retries = 5
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                self.show_status("WIFI", f"Connecting... {retry_count+1}/{max_retries}")
                
                network = self.matrixportal.network
                esp = network._wifi.esp
                esp.reset()
                network.connect()
                
                # Get IP address
                ip = esp.ip_address
                ip_str = f"{ip[0]}.{ip[1]}.{ip[2]}.{ip[3]}"
                print(f"Connected! IP: {ip_str}")
                
                self.show_status("WIFI", f"Connected: {ip_str}")
                time.sleep(2)
                return True
                
            except Exception as e:
                print(f"WiFi connection attempt {retry_count+1} failed: {e}")
                retry_count += 1
                time.sleep(5)
                
        self.show_status("ERROR", "WiFi Failed")
        return False
    
    def setup_mqtt(self):
        """Setup MQTT client with reconnection"""
        try:
            from secrets import secrets
            
            # Set update server from secrets
            self.update_server = secrets.get('update_server', 'http://10.0.1.100:8000')
            
            # Get device ID from secrets
            self.device_id = secrets.get('device_id', 'default')
            
            # Create MQTT client
            socket.set_interface(self.matrixportal.network._wifi.esp)
            self.mqtt = MQTT.MQTT(
                broker=secrets.get('mqtt_broker', '10.0.1.101'),
                port=secrets.get('port', 1883),
                username=secrets.get('mqtt_username'),
                password=secrets.get('mqtt_password'),
                socket_pool=socket,
                keep_alive=60
            )
            
            # Set up callbacks
            self.mqtt.on_connect = self.mqtt_connected
            self.mqtt.on_disconnect = self.mqtt_disconnected
            self.mqtt.on_message = self.mqtt_message_received
            
            # Connect to MQTT
            self.show_status("MQTT", "Connecting...")
            self.mqtt.connect()
            
            return True
            
        except Exception as e:
            print(f"MQTT setup failed: {e}")
            self.show_status("ERROR", f"MQTT Failed: {e}")
            return False
    
    def mqtt_connected(self, client, userdata, flags, rc):
        """MQTT connection callback"""
        print("MQTT Connected!")
        self.connection_attempts = 0
        
        # Subscribe to color-based topics and update topics
        # Support both broadcast (matrix/color) and device-specific (matrix/device_id/color)
        colors = ["red", "orange", "yellow", "blue", "green"]
        topics = []
        
        # Broadcast topics (matrix/color/...)
        for color in colors:
            topics.extend([
                f"matrix/{color}",        # Just color
                f"matrix/{color}/+",      # Color + source
                f"matrix/{color}/+/+"     # Color + source + animation
            ])
        
        # Device-specific topics (matrix/device_id/color/...)
        for color in colors:
            topics.extend([
                f"matrix/{self.device_id}/{color}",        # Device + color
                f"matrix/{self.device_id}/{color}/+",      # Device + color + source
                f"matrix/{self.device_id}/{color}/+/+"     # Device + color + source + animation
            ])
        
        # Update topics
        topics.extend([
            "matrix/update/check",         # Broadcast update check
            "matrix/update/deploy",        # Broadcast update deploy
            f"matrix/{self.device_id}/update/check",   # Device-specific update check
            f"matrix/{self.device_id}/update/deploy"   # Device-specific update deploy
        ])
        
        for topic in topics:
            try:
                client.subscribe(topic)
                print(f"Subscribed to {topic}")
            except Exception as e:
                print(f"Failed to subscribe to {topic}: {e}")
        
        self.show_status("READY", "System Ready")
    
    def mqtt_disconnected(self, client, userdata, rc):
        """MQTT disconnection callback"""
        print("MQTT Disconnected")
        self.connection_attempts += 1
        self.show_status("ERROR", "MQTT Disconnected")
    
    def mqtt_message_received(self, client, topic, message):
        """Handle incoming MQTT messages"""
        try:
            topic_parts = topic.split('/')
            if len(topic_parts) >= 2:
                # Check if this is a device-specific message
                is_device_specific = False
                color_index = 1  # Default position for broadcast messages
                
                # Check if second part is our device ID
                if len(topic_parts) >= 3 and topic_parts[1] == self.device_id:
                    is_device_specific = True
                    color_index = 2  # Color is at position 2 for device-specific messages
                
                # Handle update commands
                update_index = color_index if is_device_specific else 1
                if len(topic_parts) > update_index and topic_parts[update_index] == "update":
                    if len(topic_parts) > update_index + 1:
                        if topic_parts[update_index + 1] == "check":
                            print(f"Update check requested ({'device-specific' if is_device_specific else 'broadcast'})")
                            self.check_for_updates()
                        elif topic_parts[update_index + 1] == "deploy":
                            print(f"Update deploy requested ({'device-specific' if is_device_specific else 'broadcast'})")
                            self.deploy_update()
                    return
                
                # Handle regular color messages
                if len(topic_parts) > color_index:
                    color = topic_parts[color_index]     # red, orange, yellow, blue, green
                    source = topic_parts[color_index + 1] if len(topic_parts) > color_index + 1 else "unknown"
                    
                    # Check if third parameter (relative to color) is 'alert' or animation
                    third_param = topic_parts[color_index + 2] if len(topic_parts) > color_index + 2 else None
                    is_alert_mode = (source == "alert") or (third_param == "alert")
                    animation = third_param if third_param and third_param != "alert" else None
                
                    # Parse message - expect JSON or plain text
                    try:
                        msg_data = json.loads(message)
                        text = msg_data.get('text', str(message))
                        duration = msg_data.get('duration')
                    except:
                        text = str(message)
                        duration = None
                    
                    print(f"Received {color} from {source}: {text} (animation: {animation}) [{'device-specific' if is_device_specific else 'broadcast'}]")
                    
                    # Add to message queue
                    needs_display = self.message_queue.add_message(
                        color, text, source, duration, animation, is_alert_mode
                    )
                    
                    # If high priority, display immediately
                    if needs_display:
                        self.display_current_message()
                    
        except Exception as e:
            print(f"Error processing MQTT message: {e}")
    
    def display_current_message(self):
        """Display the current message with appropriate formatting"""
        if self.message_queue.current_message is None:
            return
            
        msg = self.message_queue.current_message
        color = msg['priority_name']
        text = msg['text']
        animation = msg.get('animation')
        is_alert = msg.get('is_alert', False)
        
        # Get color scheme based on alert mode
        color_modes = COLORS.get(color, COLORS['blue'])
        colors = color_modes['alert'] if is_alert else color_modes['normal']
        
        try:
            # Handle animations first
            if animation and animation in ANIMATIONS:
                self.run_animation(animation, text, colors)
                return
            
            # Format text first to get accurate length for scrolling decision
            formatted_text = self.format_text(text, color)
            
            # Determine if text needs scrolling (display shows ~11-12 characters)
            # Only scroll if text is significantly longer than display
            needs_scrolling = len(formatted_text) > 15
            
            print(f"DEBUG: Color={color}, Original='{text}', Formatted='{formatted_text}', Length={len(formatted_text)}, Scrolling={needs_scrolling}")
            print(f"DEBUG: Colors object: {colors}")
            print(f"DEBUG: Flash setting: {colors.get('flash', False)}")
            
            if needs_scrolling:
                print(f"DEBUG: Using scrolling display for {color}")
                self.display_scrolling_text(formatted_text, colors)
            else:
                print(f"DEBUG: Using static display for {color}")
                self.display_static_text(formatted_text, colors, color)
            
            # Force display update
            gc.collect()
            
        except Exception as e:
            print(f"Display error: {e}")
    
    def display_static_text(self, text, colors, color):
        """Display static text with flashing if needed"""
        # Stop scrolling
        self.is_scrolling = False
        
        # Text is already formatted, just truncate if needed for static display
        display_text = text
        if len(display_text) > 11:
            display_text = display_text[:8] + "..."
        
        
        # Set background and colors (no more flashing logic)
        self.matrixportal.set_background(colors['bg'])
        self.matrixportal.set_text_color(colors['fg'], 0)
        
        self.matrixportal.set_text(display_text, 0)
    
    def display_scrolling_text(self, text, colors):
        """Display scrolling text for long messages"""
        # Set background and colors (no more flashing logic)
        self.matrixportal.set_background(colors['bg'])
        self.matrixportal.set_text_color(colors['fg'], 0)
        
        # Text is already formatted, just clean up any remaining separators
        display_text = text
        
        # CircuitPython 7.3.1 compatible scrolling - use only layer 0
        self.scroll_text = display_text + "    "  # Add padding
        self.scroll_position = 0
        self.is_scrolling = True
        self.scroll_colors = colors
        
        # Start with first portion of text
        initial_text = self.scroll_text[:11] if len(self.scroll_text) >= 11 else self.scroll_text
        self.matrixportal.set_text(initial_text, 0)
    
    def update_scrolling(self):
        """Update manual scrolling for long text"""
        if not self.is_scrolling or not self.scroll_text:
            return
            
        # Scroll every 0.4 seconds (slower for readability)
        if time.monotonic() - self.last_scroll_time > 0.4:
            self.last_scroll_time = time.monotonic()
            
            # Display width is approximately 11-12 characters
            display_width = 11
            
            # Get the visible portion of text
            if self.scroll_position + display_width <= len(self.scroll_text):
                visible_text = self.scroll_text[self.scroll_position:self.scroll_position + display_width]
            else:
                # Wrap around
                remaining = len(self.scroll_text) - self.scroll_position
                visible_text = self.scroll_text[self.scroll_position:] + self.scroll_text[:display_width - remaining]
            
            
            # Update display on layer 0
            self.matrixportal.set_text(visible_text, 0)
            
            # Advance position
            self.scroll_position += 1
            if self.scroll_position >= len(self.scroll_text):
                self.scroll_position = 0
    
    def run_animation(self, animation_name, text, colors):
        """Run predefined animations"""
        animation = ANIMATIONS.get(animation_name, {})
        pattern = animation.get('pattern', 'pulse')
        
        # Initialize animation if not running
        if self.animation_state != animation_name:
            self.animation_state = animation_name
            self.animation_frame = 0
            self.animation_start_time = time.monotonic()
        
        # Simple animation patterns
        if pattern == 'pulse':
            self.animate_pulse(text, colors)
        elif pattern == 'flash_border':
            self.animate_flash_border(text, colors)
        elif pattern == 'slide_left':
            self.animate_slide_left(text, colors)
        else:
            # Default to static display
            self.display_static_text(text, colors, self.message_queue.current_message['priority_name'])
    
    def animate_pulse(self, text, colors):
        """Pulse animation by alternating brightness"""
        elapsed = time.monotonic() - self.animation_start_time
        if elapsed > 0.5:
            self.flash_state = not self.flash_state
            self.animation_start_time = time.monotonic()
        
        if self.flash_state:
            self.matrixportal.set_background(colors['bg'])
            self.matrixportal.set_text_color(colors['fg'], 0)
        else:
            self.matrixportal.set_background(0x000000)
            self.matrixportal.set_text_color(colors['fg'], 0)
        
        self.matrixportal.set_text(self.format_text(text, 'orange'), 0)
    
    def animate_flash_border(self, text, colors):
        """Flash border effect (simplified)"""
        elapsed = time.monotonic() - self.animation_start_time
        if elapsed > 0.3:
            self.flash_state = not self.flash_state
            self.animation_start_time = time.monotonic()
        
        # Alternate between normal and inverted colors
        if self.flash_state:
            self.matrixportal.set_background(colors['fg'])
            self.matrixportal.set_text_color(colors['bg'], 0)
        else:
            self.matrixportal.set_background(colors['bg'])
            self.matrixportal.set_text_color(colors['fg'], 0)
        
        self.matrixportal.set_text(self.format_text(text, 'orange'), 0)
    
    def animate_slide_left(self, text, colors):
        """Slide text from right to left"""
        # Use scrolling for slide effect
        self.display_scrolling_text(f"    {text}    ", colors)
    
    def format_text(self, text, color):
        """Format text for optimal display"""
        original_text = text
        
        # Replace common separators
        text = text.replace('_', ' ')
        text = text.replace('-', ' ')
        
        # Only truncate for static display, let scrolling handle long text
        # For static display, truncate if longer than display width
        if len(text) > 11:
            # Don't truncate here - let scrolling handle it
            pass
        
        # Add color indicators (shorter for small display)
        if color == 'red':
            text = f"!{text}!"
        elif color == 'orange':
            text = f">{text}<"
        
        print(f"DEBUG: format_text: '{original_text}' -> '{text}' (color={color})")
        return text
    
    def show_status(self, category, message):
        """Show system status message"""
        # Keep status messages short for small display
        if category == "READY":
            display_msg = "READY"
        elif category == "WIFI":
            if "Connected:" in message:
                # Extract just the IP for display
                ip = message.split(": ")[1] if ": " in message else message
                display_msg = f"IP:\n{ip}"
            else:
                display_msg = f"WIFI\n{message}"
        else:
            display_msg = f"{category}\n{message}"
            
        self.message_queue.add_message('green', display_msg, 'system', None, None, False)
        self.display_current_message()
    
    def reconnect_mqtt(self):
        """Attempt to reconnect MQTT"""
        if self.connection_attempts < 10:
            try:
                self.show_status("MQTT", f"Reconnecting... {self.connection_attempts}")
                self.mqtt.reconnect()
                return True
            except Exception as e:
                print(f"MQTT reconnect failed: {e}")
                self.connection_attempts += 1
                return False
        return False
    
    def process_message_queue(self):
        """Process message queue and advance messages"""
        # Check if we should advance to next message
        if self.message_queue.should_advance():
            next_msg = self.message_queue.get_next_message()
            if next_msg:
                self.message_queue.current_message = next_msg
                self.message_queue.display_start_time = time.monotonic()
                self.display_current_message()
            elif self.message_queue.current_message is not None:
                # No more messages, clear display for non-green messages
                if self.message_queue.current_message['priority_name'] != 'green':
                    self.message_queue.current_message = None
                    self.clear_display()
        
        # No more flashing updates needed
    
    def clear_display(self):
        """Clear the display"""
        try:
            self.is_scrolling = False
            self.matrixportal.set_background(0x000000)
            self.matrixportal.set_text("", 0)
        except Exception as e:
            print(f"Clear display error: {e}")
    
    def heartbeat(self):
        """Send heartbeat and perform maintenance"""
        now = time.monotonic()
        if now - self.last_heartbeat > 30:  # Every 30 seconds
            self.last_heartbeat = now
            
            # Publish heartbeat if MQTT is connected
            try:
                if self.mqtt and self.mqtt.is_connected():
                    heartbeat_data = {
                        'status': 'online',
                        'uptime': now,
                        'free_memory': gc.mem_free(),
                        'queue_size': len(self.message_queue.messages)
                    }
                    self.mqtt.publish('matrix/heartbeat', json.dumps(heartbeat_data))
            except Exception as e:
                print(f"Heartbeat failed: {e}")
            
            # Garbage collection
            gc.collect()
    
    def check_for_updates(self):
        """Check for code updates"""
        if not self.update_server:
            print("No update server configured")
            return
            
        try:
            self.show_status("UPDATE", "Checking...")
            
            # Check version
            version_url = f"{self.update_server}/matrix/version.txt"
            print(f"Checking version at: {version_url}")
            
            response = requests.get(version_url)
            if response.status_code == 200:
                latest_version = response.text.strip()
                print(f"Current: {VERSION}, Latest: {latest_version}")
                
                if latest_version != VERSION:
                    self.message_queue.add_message('yellow', f"Update: {latest_version}", 'update', 10, None, False)
                    self.display_current_message()
                    
                    # Publish update availability
                    if self.mqtt and self.mqtt.is_connected():
                        update_info = {
                            'current_version': VERSION,
                            'latest_version': latest_version,
                            'update_available': True
                        }
                        self.mqtt.publish('matrix/update/status', json.dumps(update_info))
                else:
                    self.message_queue.add_message('green', f"Up to date: {VERSION}", 'update', 5, None, False)
                    self.display_current_message()
            else:
                self.show_status("ERROR", f"Update check failed: {response.status_code}")
                
        except Exception as e:
            print(f"Update check failed: {e}")
            self.show_status("ERROR", "Update check failed")
    
    def deploy_update(self):
        """Download and deploy code update"""
        if not self.update_server:
            print("No update server configured")
            return
            
        try:
            self.show_status("UPDATE", "Downloading...")
            
            # Download new code
            code_url = f"{self.update_server}/matrix/code.py"
            print(f"Downloading from: {code_url}")
            
            response = requests.get(code_url)
            if response.status_code == 200:
                # Write new code to backup file first
                with open("code.py.new", "w") as f:
                    f.write(response.text)
                
                self.show_status("UPDATE", "Restarting...")
                time.sleep(2)
                
                # Replace current code and restart
                try:
                    with open("code.py.new", "r") as new_file:
                        new_code = new_file.read()
                    with open("code.py", "w") as current_file:
                        current_file.write(new_code)
                    
                    print("Update deployed, restarting...")
                    microcontroller.reset()
                    
                except Exception as e:
                    print(f"File replacement failed: {e}")
                    self.show_status("ERROR", "Update failed")
            else:
                self.show_status("ERROR", f"Download failed: {response.status_code}")
                
        except Exception as e:
            print(f"Update deployment failed: {e}")
            self.show_status("ERROR", "Update failed")
    
    def auto_update_check(self):
        """Automatically check for updates periodically"""
        now = time.monotonic()
        # Check for updates every 24 hours
        if now - self.last_update_check > 86400:  # 24 hours
            self.last_update_check = now
            print("Performing automatic update check...")
            self.check_for_updates()
    
    def run(self):
        """Main application loop"""
        print(f"=== MatrixPortal Message Display v{VERSION} ===")
        
        # Initialize systems
        if not self.setup_network():
            return
            
        if not self.setup_mqtt():
            return
        
        print("System ready! Listening for messages...")
        
        # Show version on startup
        self.message_queue.add_message('green', f"v{VERSION}", 'system', 3, None, False)
        self.display_current_message()
        
        # Main loop
        last_mqtt_check = 0
        
        while True:
            try:
                current_time = time.monotonic()
                
                # Check MQTT connection and process messages
                if current_time - last_mqtt_check > 1:  # Every second
                    last_mqtt_check = current_time
                    
                    if self.mqtt:
                        if self.mqtt.is_connected():
                            self.mqtt.loop(timeout=0.1)  # Non-blocking
                        else:
                            # Try to reconnect
                            if not self.reconnect_mqtt():
                                time.sleep(5)  # Wait before next attempt
                
                # Process message queue
                self.process_message_queue()
                
                # Update scrolling
                self.update_scrolling()
                
                # Send heartbeat
                self.heartbeat()
                
                # Check for updates (daily)
                self.auto_update_check()
                
                # Small delay to prevent tight loop
                time.sleep(0.1)
                
            except KeyboardInterrupt:
                print("Shutting down...")
                break
            except Exception as e:
                print(f"Main loop error: {e}")
                time.sleep(1)  # Prevent rapid error loops

# Initialize and run the controller
if __name__ == "__main__":
    controller = MatrixController()
    controller.run()