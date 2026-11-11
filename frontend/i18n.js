// UI language support. The language is the first URL segment (/en/... or /es/...).
// English is the source language: templates and messages are written in English, and on /es/ pages
//  - t('English text', {vars}) returns the Spanish text (explicit use: alerts, confirms, built strings),
//  - a MutationObserver translates exact-match text, placeholders, titles and aria labels of everything rendered.
// Anything without a dictionary entry simply stays in English.

const hasLocation = typeof location !== 'undefined';
const match = hasLocation ? location.pathname.match(/^\/(en|es)(?=\/|$)/) : null;
export const lang = match ? match[1] : (typeof document !== 'undefined' && document.documentElement?.lang === 'es' ? 'es' : 'en');
export const locale = lang === 'es' ? 'es' : 'en-US';
/** Path of the current page without the language prefix ('/sign-in', '/'). */
export const appPath = hasLocation ? (location.pathname.replace(/^\/(en|es)(?=\/|$)/, '') || '/') : '/';
/** Prefix an app path with the active language: L('/sign-in') -> '/es/sign-in'. */
export const L = path => `/${lang}${path}`;

const ES = {
  // ---- navigation / shell ----
  'Dashboard': 'Panel', 'Campaigns': 'Campañas', 'AI Studio': 'AI Studio', 'Creatives': 'Creatividades', 'Video': 'Video',
  'Optimizer': 'Optimizador', 'WhatsApp': 'WhatsApp', 'Copilot': 'Copiloto', 'Connections': 'Conexiones',
  'Open navigation': 'Abrir navegación', 'Close navigation': 'Cerrar navegación',
  'Profile settings': 'Ajustes del perfil', 'Log out': 'Cerrar sesión',
  'About': 'Acerca de', 'Contact': 'Contacto', 'Terms': 'Términos', 'Privacy': 'Privacidad', 'Legal': 'Legal', 'Language': 'Idioma',
  'Loading…': 'Cargando…',
  // ---- dashboard ----
  '· last 14 days': '· últimos 14 días', 'Remove sample data': 'Quitar datos de ejemplo',
  'Spend': 'Gasto', 'Revenue': 'Ingresos', 'Conversions': 'Conversiones', 'Platform': 'Plataforma', 'Clicks': 'Clics',
  'spend': 'gasto', 'revenue': 'ingresos',
  'Delete the sample campaigns? Your real campaigns are kept.': '¿Eliminar las campañas de ejemplo? Tus campañas reales se conservan.',
  'Showing <b>sample data</b> until you connect your own ad accounts. ': 'Se muestran <b>datos de ejemplo</b> hasta que conectes tus propias cuentas publicitarias. ',
  // ---- campaigns ----
  'Name': 'Nombre', 'Objective': 'Objetivo', 'Daily budget ($)': 'Presupuesto diario ($)', 'Daily budget': 'Presupuesto diario',
  'My brand': 'Mi marca', 'Using your brand': 'Usando tu marca', 'Edit': 'Editar', 'Ideal customer': 'Cliente ideal', 'Tone': 'Tono', 'Products': 'Productos',
  'Every creative, video, copy and WhatsApp reply now uses this. You never have to enter it again.': 'Cada creatividad, video, texto y respuesta de WhatsApp usa esto. No tendrás que escribirlo de nuevo.',
  'Create my first creatives': 'Crear mis primeras creatividades', 'Edit my brand': 'Editar mi marca', 'Back': 'Atrás', 'Finish': 'Terminar', 'Next': 'Siguiente', 'Your brand': 'Tu marca', 'Buy now': 'Compra ya', 'Color': 'Color', 'Remove': 'Quitar', 'Add color': 'Agregar color', 'Pick at least one color, or skip this step.': 'Elige al menos un color, o salta este paso.',
  'Add at least your business name, one product and your ideal customer.': 'Agrega al menos el nombre de tu negocio, un producto y tu cliente ideal.',
 'Headline': 'Titular',
  'Landing URL': 'URL de destino', 'Create campaign': 'Crear campaña', 'Status': 'Estado', 'Budget/day': 'Presupuesto/día',
  'Spend 7d': 'Gasto 7d', 'Pause': 'Pausar', 'Resume': 'Reanudar', 'Delete campaign?': '¿Eliminar la campaña?',
  'sales': 'ventas', 'traffic': 'tráfico', 'leads': 'leads', 'awareness': 'reconocimiento', 'whatsapp': 'whatsapp',
  'active': 'activa', 'paused': 'en pausa',
  'Saved locally; platform sync failed: {e}': 'Guardada localmente; falló la sincronización con la plataforma: {e}',
  // ---- AI Studio ----
  '· ad copy generator': '· generador de textos publicitarios', 'Product': 'Producto', 'Audience': 'Audiencia', 'Tone': 'Tono',
  'Generate': 'Generar', 'Organic running shoes': 'Zapatillas de running orgánicas',
  'friendly': 'amistoso', 'bold': 'atrevido', 'professional': 'profesional', 'playful': 'divertido', 'urgent': 'urgente',
  'runners 25-45': 'corredores de 25 a 45 años',
  'Template mode – set ANTHROPIC_API_KEY for LLM copy.': 'Modo plantillas: configura ANTHROPIC_API_KEY para textos con IA.',
  // languages / themes (names come from the server)
  'English': 'Inglés', 'Spanish': 'Español', 'Portuguese': 'Portugués', 'French': 'Francés', 'German': 'Alemán', 'Italian': 'Italiano',
  'purple': 'morado', 'night': 'noche', 'pink': 'rosa', 'light': 'claro', 'dark': 'oscuro',
  // ---- creatives ----
  'Creative Studio': 'Estudio de Creatividades', 'Video Studio': 'Estudio de Video',
  '· image ads sized for each platform': '· anuncios de imagen con el tamaño de cada plataforma',
  'Product name': 'Nombre del producto', 'Description (facts only)': 'Descripción (solo hechos)',
  'What it is, flavor, size, materials…': 'Qué es, sabor, tamaño, materiales…', 'Theme': 'Tema',
  'Price badge (optional)': 'Etiqueta de precio (opcional)', 'Rating line (optional, must be true)': 'Línea de valoración (opcional, debe ser verdadera)',
  '4.8/5 from 1,200 reviews': '4,8/5 con 1.200 reseñas', 'Product image (PNG, transparent bg works best)': 'Imagen del producto (PNG, funciona mejor con fondo transparente)',
  'Product image (PNG, transparent bg)': 'Imagen del producto (PNG, fondo transparente)',
  'Real customer testimonials (optional, one per line:': 'Testimonios reales de clientes (opcional, uno por línea: ',
  'Platforms': 'Plataformas', 'Generate creatives': 'Generar creatividades', 'Generating…': 'Generando…',
  'AI scene backgrounds': 'Fondos de escena con IA',
  'Scene idea, e.g. cozy bedroom at night (don\'t name the product)': 'Idea de escena, p. ej. dormitorio acogedor de noche (no nombres el producto)',
  'Reviews, ratings and customer counts are never invented — they only appear if you enter them. 9:16 formats keep text inside platform safe zones.':
    'Las reseñas, valoraciones y cifras de clientes nunca se inventan: solo aparecen si las escribes tú. Los formatos 9:16 mantienen el texto dentro de las zonas seguras de cada plataforma.',
  'Download': 'Descargar', 'Attach to campaign…': 'Adjuntar a una campaña…', 'Pick at least one platform': 'Elige al menos una plataforma',
  'Image over 8MB': 'La imagen supera los 8 MB', 'Delete creative?': '¿Eliminar la creatividad?',
  'Attached – campaign text updated': 'Adjuntada: se actualizó el texto de la campaña', 'Scene cost: ${c}': 'Costo de escena: ${c}',
  'Remove background': 'Quitar fondo', 'Choose an image first': 'Primero elige una imagen',
  'Removing background… (the first run downloads a ~180MB model, can take a minute)': 'Quitando el fondo… (la primera vez descarga un modelo de ~180 MB y puede tardar un minuto)',
  'Done ({w}×{h}) – this transparent PNG will be used.': 'Listo ({w}×{h}): se usará este PNG transparente.',
  'Not installed (pip install rembg onnxruntime opencv-python-headless numpy)': 'No instalado (pip install rembg onnxruntime opencv-python-headless numpy)',
  // ---- video ----
  '· AI background video + your product, text and CTA': '· video de fondo con IA + tu producto, texto y CTA',
  'Scene idea (don\'t name the product)': 'Idea de escena (no nombres el producto)', 'cozy bedroom at night': 'dormitorio acogedor de noche',
  'Length': 'Duración', 'Quality': 'Calidad', 'Draft 480p (~$0.21/s)': 'Borrador 480p (~$0.21/s)', 'Standard 720p (~$0.46/s)': 'Estándar 720p (~$0.46/s)',
  'Estimate & generate': 'Estimar y generar', 'No videos yet.': 'Aún no hay videos.', 'Check again': 'Comprobar de nuevo', 'Cancel (refund)': 'Cancelar (reembolso)',
  'Meta Reel (9:16)': 'Meta Reel (9:16)', 'Meta Feed (1:1)': 'Meta Feed (1:1)', 'TikTok (9:16)': 'TikTok (9:16)', 'Google / YouTube (16:9)': 'Google / YouTube (16:9)',
  '9:16 serves Meta Reels and TikTok from one clip. Every extra aspect ratio is another generation: slower and billed.': 'El formato 9:16 sirve para Meta Reels y TikTok con un solo clip. Cada proporción adicional es otra generación: más lenta y se cobra.',
  'One background clip is generated per aspect ratio and shared across platforms. Your real product photo is composited on top, never redrawn. No audio. You\'ll see the estimated cost and confirm before anything is billed.':
    'Se genera un clip de fondo por proporción de pantalla y se comparte entre plataformas. Tu foto real del producto se superpone, nunca se redibuja. Sin audio. Verás el costo estimado y confirmarás antes de que se cobre algo.',
  'Add your Higgsfield API key on the <a href="#connections">Connections</a> page to enable video.': 'Agrega tu clave de API de Higgsfield en la página de <a href="#connections">Conexiones</a> para habilitar el video.',
  'AI scene backgrounds: add your Higgsfield API key on the <a href="#connections">Connections</a> page to enable': 'Fondos de escena con IA: agrega tu clave de API de Higgsfield en la página de <a href="#connections">Conexiones</a> para habilitarlos',
  'Adding your product, text and CTA…': 'Añadiendo tu producto, texto y CTA…',
  'Rendering at Higgsfield… {t} (usually a few minutes)': 'Renderizando en Higgsfield… {t} (normalmente unos minutos)',
  'Waiting in Higgsfield\'s queue… {t}': 'En la cola de Higgsfield… {t}', 'Starting… {t}': 'Iniciando… {t}', 'failed': 'falló', 'Request {id}': 'Solicitud {id}',
  'Estimated ${est} exceeds your cap of ${cap} (VIDEO_MAX_USD). Try fewer platforms, a shorter clip or Draft quality.':
    'El costo estimado de ${est} supera tu límite de ${cap} (VIDEO_MAX_USD). Prueba con menos plataformas, un clip más corto o calidad Borrador.',
  'Estimated cost: ~${est} ({n} clip(s)). Billed to your Higgsfield balance. Generate?': 'Costo estimado: ~${est} ({n} clip(s)). Se cobra a tu saldo de Higgsfield. ¿Generar?',
  'Cancel this clip? Higgsfield refunds it if still queued.': '¿Cancelar este clip? Higgsfield lo reembolsa si sigue en cola.', 'Delete video?': '¿Eliminar el video?',
  // ---- optimizer ----
  'No recommendations – all campaigns look healthy.': 'Sin recomendaciones: todas las campañas se ven saludables.',
  'Open AI Studio →': 'Abrir AI Studio →', 'Apply: pause campaign': 'Aplicar: pausar campaña', 'Apply: set budget {v}': 'Aplicar: fijar presupuesto en {v}',
  'pause': 'pausar', 'budget': 'presupuesto', 'creative': 'creatividad', 'Platform sync failed: {e}': 'Falló la sincronización con la plataforma: {e}',
  // ---- WhatsApp ----
  'WhatsApp Sales': 'Ventas por WhatsApp', '{mode} mode': 'modo {mode}', 'demo': 'demo', 'live': 'en vivo',
  'Demo mode: messages are simulated, nothing is sent. Add your WhatsApp credentials on the Connections page to go live.': 'Modo demo: los mensajes se simulan y no se envía nada. Agrega tus credenciales de WhatsApp en la página de Conexiones para pasar a producción.',
  'Lead': 'Lead', 'Stage': 'Etapa', 'Campaign': 'Campaña', 'No leads yet': 'Aún no hay leads', 'Simulate a customer': 'Simular un cliente',
  'Phone': 'Teléfono', 'From campaign': 'Desde la campaña', '(none)': '(ninguna)', 'Message': 'Mensaje', 'Send': 'Enviar',
  'Hi, how much is it?': 'Hola, ¿cuánto cuesta?', 'Sales knowledge': 'Conocimiento de ventas', '(the only facts the AI may use)': '(los únicos datos que la IA puede usar)',
  'Save': 'Guardar', 'Saved': 'Guardado', 'Run follow-ups now': 'Ejecutar seguimientos ahora', 'Select a lead.': 'Selecciona un lead.',
  'Reply as human (pauses AI)…': 'Responder como persona (pausa la IA)…', 'Give back to AI': 'Devolver a la IA', 'Take over': 'Tomar el control', 'Mark sold…': 'Marcar como vendido…',
  'Sale value ($)?': '¿Valor de la venta ($)?', '{n} follow-ups sent': '{n} seguimientos enviados',
  'new': 'nuevo', 'engaged': 'interesado', 'qualified': 'calificado', 'won': 'ganado', 'lost': 'perdido',
  // ---- Copilot ----
  'Ask about your campaigns in plain words. I use your last 7 days of data and your brand profile.': 'Pregúntame sobre tus campañas con tus propias palabras. Uso tus últimos 7 días de datos y el perfil de tu marca.',
  'Hi! I can help you decide what to scale, pause or improve. Pick a question below or write your own.': '¡Hola! Te ayudo a decidir qué escalar, pausar o mejorar. Elige una pregunta abajo o escribe la tuya.',
  'Try asking:': 'Prueba preguntar:', 'Thinking…': 'Pensando…', 'Your question': 'Tu pregunta',
  'e.g. Which campaign should I scale?': 'Ej.: ¿Qué campaña debería escalar?',
  'Which campaign is losing money and should I pause?': '¿Qué campaña está perdiendo dinero y debería pausar?',
  'How did my ads perform this week?': '¿Cómo me fue con mis anuncios esta semana?',
  'Where should I move my budget to sell more?': '¿Dónde debería mover mi presupuesto para vender más?',
  'Which platform gives me the best return?': '¿Qué plataforma me da el mejor retorno?',
  'What should I improve first?': '¿Qué debería mejorar primero?',
  'This week you spent {spend} and sold {revenue}.': 'Esta semana invertiste {spend} y vendiste {revenue}.',
  'Nothing spent or sold this week yet. Create a campaign to get started.': 'Esta semana aún no has invertido ni vendido. Crea una campaña para empezar.',
  'Spent and sold, last 14 days': 'Invertido y vendido, últimos 14 días',
  'Advanced detail (ROAS, CTR, CPA…)': 'Detalle avanzado (ROAS, CTR, CPA…)',
  'ROAS: revenue per peso spent. CTR: share of people who clicked. CPA: cost per sale.': 'ROAS: ingresos por cada peso invertido. CTR: porcentaje de personas que hicieron clic. CPA: costo por venta.',
  'I want people to know me': 'Quiero que me conozcan',
  'Reach new people who could like your brand.': 'Llega a personas nuevas que podrían querer tu marca.',
  'I want people to buy': 'Quiero que me compren',
  'Get new customers and remind recent visitors.': 'Consigue clientes nuevos y recuerda a quienes te visitaron.',
  'Win back those who did not buy': 'Recuperar a los que no compraron',
  'Talk again to people who showed interest and left.': 'Vuelve a hablarle a quienes mostraron interés y se fueron.',
  'Be known': 'Que te conozcan',
  'Visits to your page': 'Visitas a tu página',
  'Sales': 'Ventas',
  'Contacts': 'Contactos',
  'WhatsApp chats': 'Chats de WhatsApp',
  'What do you want to achieve?': '¿Qué quieres lograr?',
  'Pick a goal and the app builds the campaign structure for you. You review it before anything is created.': 'Elige un objetivo y la app arma la estructura de campañas por ti. La revisas antes de que se cree nada.',
  'Product': 'Producto',
  'Total daily budget': 'Presupuesto diario total',
  'Page or store link (optional)': 'Enlace de tu página o tienda (opcional)',
  'Build my plan': 'Armar mi plan',
  'Create a campaign manually (advanced)': 'Crear una campaña manualmente (avanzado)',
  'Building your plan…': 'Armando tu plan…',
  'Your plan': 'Tu plan',
  'per day': 'por día',
  'Who sees it': 'Quién lo ve',
  'Ad': 'Anuncio',
  'Total': 'Total',
  'Create these campaigns': 'Crear estas campañas',
  'Studio': 'Estudio',
  'goal, campaigns and creatives in one place': 'objetivo, campañas y creatividades en un solo lugar',
  'Your creatives': 'Tus creatividades',
  'Want new campaigns?': '¿Quieres campañas nuevas?',
  'Pick a goal in the Studio and the app builds the campaigns and creatives for you.': 'Elige un objetivo en el Estudio y la app arma las campañas y las creatividades por ti.',
  'Go to the Studio': 'Ir al Estudio',
  'No creatives yet. Build a plan above and they appear here.': 'Aún no hay creatividades. Arma un plan arriba y aparecerán aquí.',
  'Create a creative manually (advanced)': 'Crear una creatividad manualmente (avanzado)',
  'Write ad copy (advanced)': 'Escribir textos de anuncios (avanzado)',
  'Campaigns created. Making your creatives…': 'Campañas creadas. Preparando tus creatividades…',
  'Done! Campaigns and creatives are ready. Attach a creative to each campaign below.': '¡Listo! Las campañas y las creatividades están listas. Adjunta una creatividad a cada campaña aquí abajo.',
  'Campaigns created, but creatives failed: {e}': 'Campañas creadas, pero fallaron las creatividades: {e}',
  'Which campaign should I scale?': '¿Qué campaña debería escalar?', 'Ask': 'Preguntar', 'Error: {e}': 'Error: {e}',
  // ---- Connections ----
  'These credentials belong to <b>your account only</b>. They are stored encrypted and never shown again after saving - leave a secret blank to keep it.':
    'Estas credenciales pertenecen <b>solo a tu cuenta</b>. Se guardan cifradas y no se vuelven a mostrar tras guardarlas: deja un secreto en blanco para conservar el guardado.',
  'How do I get my access token? Step-by-step guide': '¿Cómo obtengo mi token de acceso? Guía paso a paso',
  'Also used for Instagram placements.': 'También se usa para ubicaciones en Instagram.',
  'In Meta\'s WhatsApp webhook settings, set the callback URL and verify token shown below, and subscribe to "messages".': 'En los ajustes del webhook de WhatsApp de Meta, configura la URL de callback y el token de verificación que se muestran abajo, y suscríbete a «messages».',
  'Callback URL:': 'URL de callback:', ' (saved)': ' (guardado)', 'Test & import campaigns': 'Probar e importar campañas', 'Disconnect': 'Desconectar', 'Re-check': 'Volver a comprobar',
  '● checking…': '● comprobando…', '● not connected': '● no conectado', '● connected': '● conectado', '● credentials rejected': '● credenciales rechazadas',
  '● connected, no balance': '● conectado, sin saldo', '● could not verify': '● no se pudo verificar', '● server key set, sharing off': '● clave del servidor configurada, compartir desactivado',
  ' (server key)': ' (clave del servidor)', 'Contacting {svc}…': 'Contactando con {svc}…',
  'Connection works. Imported {n} new campaign(s).': 'La conexión funciona. Se importaron {n} campaña(s) nueva(s).',
  'Remove the saved {svc} credentials?': '¿Quitar las credenciales guardadas de {svc}?',
  // service titles / field labels / hints (from the server)
  'Meta (Facebook + Instagram)': 'Meta (Facebook + Instagram)', 'TikTok Ads': 'TikTok Ads', 'Google Ads': 'Google Ads', 'WhatsApp Cloud API': 'WhatsApp Cloud API',
  'Higgsfield (AI scenes + video)': 'Higgsfield (escenas + video con IA)', 'Access token': 'Token de acceso', 'Ad account ID': 'ID de cuenta publicitaria',
  'Facebook Page ID': 'ID de página de Facebook', 'Instagram account ID': 'ID de cuenta de Instagram', 'Advertiser ID': 'ID de anunciante',
  'OAuth access token': 'Token de acceso OAuth', 'Developer token': 'Token de desarrollador', 'Customer ID': 'ID de cliente',
  'Manager (login) customer ID': 'ID de cliente administrador (login)', 'Phone number ID': 'ID del número de teléfono', 'App secret': 'Secreto de la app',
  'Webhook verify token': 'Token de verificación del webhook', 'Approved template name': 'Nombre de plantilla aprobada', 'API key': 'Clave de API',
  'System-user or long-lived token with ads_management': 'Token de usuario del sistema o de larga duración con ads_management',
  'Needed later to publish ads': 'Necesario después para publicar anuncios', 'Needed later to run ads on Instagram': 'Necesario después para mostrar anuncios en Instagram',
  'Only if you use an MCC': 'Solo si usas una cuenta MCC', 'Verifies webhook signatures - strongly recommended': 'Verifica las firmas del webhook; muy recomendado',
  'Auto-generated; paste it into Meta\'s webhook settings': 'Se genera automáticamente; pégalo en los ajustes del webhook de Meta',
  'For messages outside the 24h window': 'Para mensajes fuera de la ventana de 24 h', 'ID:SECRET - generations are billed to this account': 'ID:SECRETO: las generaciones se cobran a esta cuenta',
  // connection health details
  'Higgsfield rejected the API key': 'Higgsfield rechazó la clave de API', 'Top up your Higgsfield API balance': 'Recarga el saldo de tu API de Higgsfield',
  'Key must look like ID:SECRET': 'La clave debe tener el formato ID:SECRET', 'Server key is configured but sharing is off': 'La clave del servidor está configurada, pero compartir está desactivado',
  'Google rejected the token or developer token': 'Google rechazó el token o el token de desarrollador', 'TikTok returned an unreadable response': 'TikTok devolvió una respuesta ilegible',
  // ---- profile ----
  'Profile': 'Perfil', 'Manage the details associated with your account.': 'Gestiona los datos asociados a tu cuenta.', 'Personal information': 'Información personal',
  'Your name is visible across youradds. Your email is managed by Clerk.': 'Tu nombre es visible en youradds. Tu correo lo gestiona Clerk.', 'Email address': 'Correo electrónico',
  'No primary email': 'Sin correo principal', 'Save changes': 'Guardar cambios', 'Profile picture': 'Foto de perfil', 'Use a square image for the best fit.': 'Usa una imagen cuadrada para un mejor ajuste.',
  'Choose image': 'Elegir imagen', 'Choose profile picture': 'Elegir foto de perfil', 'PNG, JPG, GIF, or WebP': 'PNG, JPG, GIF o WebP', 'Save picture': 'Guardar foto', 'Remove picture': 'Quitar foto',
  'Selected profile picture': 'Foto de perfil seleccionada', 'Name updated.': 'Nombre actualizado.', 'Profile picture updated.': 'Foto de perfil actualizada.', 'Profile picture removed.': 'Foto de perfil eliminada.',
  'Choose a PNG, JPG, GIF, or WebP image.': 'Elige una imagen PNG, JPG, GIF o WebP.', 'Choose an image smaller than 10 MB.': 'Elige una imagen de menos de 10 MB.',
  'Could not update your profile. Please try again.': 'No se pudo actualizar tu perfil. Inténtalo de nuevo.',
  // ---- auth ----
  'Verify your email': 'Verifica tu correo', 'Verify your sign-in': 'Verifica tu inicio de sesión', 'Welcome back': 'Te damos la bienvenida', 'Create your account': 'Crea tu cuenta',
  'Reset your password': 'Restablece tu contraseña', 'Your account': 'Tu cuenta', 'Finishing sign in': 'Terminando el inicio de sesión', 'Connecting securely': 'Conectando de forma segura', 'Sign in': 'Iniciar sesión',
  'Use youradds securely with email or Google.': 'Usa youradds de forma segura con correo o Google.', 'Continue with Google': 'Continuar con Google', 'OR': 'O',
  'Email': 'Correo electrónico', 'Password': 'Contraseña', 'Forgot password?': '¿Olvidaste tu contraseña?', 'New here?': '¿Eres nuevo?', 'Create an account': 'Crea una cuenta',
  'Name (optional)': 'Nombre (opcional)', 'Confirm password': 'Confirmar contraseña', 'Create account': 'Crear cuenta', 'Already registered?': '¿Ya tienes cuenta?',
  'I agree to the <a href="{terms}" target="_blank" rel="noopener">Terms of Service</a> and acknowledge the <a href="{privacy}" target="_blank" rel="noopener">Privacy Policy</a>.':
    'Acepto los <a href="{terms}" target="_blank" rel="noopener">Términos del Servicio</a> y reconozco la <a href="{privacy}" target="_blank" rel="noopener">Política de Privacidad</a>.',
  'Enter the code sent to <b>{email}</b>.': 'Ingresa el código enviado a <b>{email}</b>.', 'Email verification code': 'Código de verificación del correo', 'Verify email': 'Verificar correo',
  'Send a new code': 'Enviar un código nuevo', 'Enter the verification code sent to your email.': 'Ingresa el código de verificación enviado a tu correo.', 'Verify device': 'Verificar dispositivo',
  'Verification code': 'Código de verificación', 'New password': 'Nueva contraseña', 'Confirm new password': 'Confirmar nueva contraseña', 'Send reset code': 'Enviar código de restablecimiento',
  'Reset password': 'Restablecer contraseña', 'Back to sign in': 'Volver a iniciar sesión', 'Current password': 'Contraseña actual', 'Update password': 'Actualizar contraseña', 'Sign out': 'Cerrar sesión',
  'SECURE SIGN-IN': 'INICIO DE SESIÓN SEGURO', 'Taking you back to youradds': 'Te llevamos de vuelta a youradds', 'Confirming your Google account and setting up your session.': 'Confirmando tu cuenta de Google y preparando tu sesión.',
  'This usually takes just a moment. You can keep this page open.': 'Normalmente tarda solo un momento. Puedes mantener esta página abierta.', 'Try Google sign-in again': 'Intentar de nuevo con Google', 'Return to sign in': 'Volver a iniciar sesión',
  'We couldn’t finish sign-in': 'No pudimos completar el inicio de sesión', 'Your Google account wasn’t connected to youradds.': 'Tu cuenta de Google no se conectó a youradds.',
  'Please try again.': 'Inténtalo de nuevo.', 'Clerk returned without an active session. Please try again.': 'Clerk respondió sin una sesión activa. Inténtalo de nuevo.',
  'Google sign-in could not be completed. Please try again.': 'No se pudo completar el inicio de sesión con Google. Inténtalo de nuevo.',
  'Checking your session': 'Comprobando tu sesión', 'Connecting securely…': 'Conectando de forma segura…',
  'Passwords do not match.': 'Las contraseñas no coinciden.', 'Please accept the Terms of Service and Privacy Policy to create an account.': 'Acepta los Términos del Servicio y la Política de Privacidad para crear una cuenta.',
  'A new verification code was sent.': 'Se envió un código de verificación nuevo.', 'If the account exists, a reset code has been sent.': 'Si la cuenta existe, se envió un código de restablecimiento.', 'Password updated.': 'Contraseña actualizada.',
  'Could not sign out. Please try again.': 'No se pudo cerrar la sesión. Inténtalo de nuevo.',
  // ---- errors shown from api()/auth ----
  'Please sign in.': 'Inicia sesión.', 'Your session ended. Please sign in again.': 'Tu sesión terminó. Inicia sesión de nuevo.',
  'Clerk is not configured.': 'Clerk no está configurado.', 'Clerk configuration is invalid.': 'La configuración de Clerk no es válida.', 'Authentication is not configured yet.': 'La autenticación aún no está configurada.',
  'Could not load authentication settings.': 'No se pudo cargar la configuración de autenticación.', 'Clerk configuration does not match this application.': 'La configuración de Clerk no coincide con esta aplicación.',
  'Could not load the authentication service.': 'No se pudo cargar el servicio de autenticación.', 'Authentication is waiting for another required step.': 'La autenticación está esperando otro paso obligatorio.',
  'This device requires a verification method that is not enabled for this app.': 'Este dispositivo requiere un método de verificación que no está habilitado para esta app.',
  'Sign-in needs another step. Contact your administrator.': 'El inicio de sesión necesita otro paso. Contacta a tu administrador.', 'Your sign-in verification expired. Start again.': 'La verificación de tu inicio de sesión caducó. Empieza de nuevo.',
  'The verification code cannot be resent. Restart sign-in.': 'No se puede reenviar el código de verificación. Reinicia el inicio de sesión.', 'Your sign-up verification expired. Start again.': 'La verificación de tu registro caducó. Empieza de nuevo.',
  'Please verify your identity in Clerk and try again.': 'Verifica tu identidad en Clerk e inténtalo de nuevo.',
  'unsupported language': 'idioma no compatible', 'campaign not found': 'campaña no encontrada', 'creative not found': 'creatividad no encontrada', 'video not found': 'video no encontrado', 'lead not found': 'lead no encontrado',
  'unknown platform': 'plataforma desconocida', 'unknown theme': 'tema desconocido', 'invalid image': 'imagen no válida', 'invalid product image': 'imagen de producto no válida', 'image too large (max 8MB)': 'imagen demasiado grande (máx. 8 MB)',
  'product image too large (max 8MB)': 'imagen de producto demasiado grande (máx. 8 MB)', 'Higgsfield key must look like ID:SECRET': 'La clave de Higgsfield debe tener el formato ID:SECRET',
  'Scene backgrounds need your Higgsfield API key (Connections page)': 'Los fondos de escena necesitan tu clave de API de Higgsfield (página de Conexiones)',
  'Video needs your Higgsfield API key (Connections page)': 'El video necesita tu clave de API de Higgsfield (página de Conexiones)',
  'cannot cancel: Higgsfield already started rendering this clip': 'no se puede cancelar: Higgsfield ya empezó a renderizar este clip', 'not authenticated': 'no autenticado',
  'already marked as won': 'ya está marcado como ganado', 'budget value required': 'se requiere un valor de presupuesto',
};

// Messages built on the server with numbers in them.
const PATTERNS = [
  [/^ROAS ([\d.]+) is below break-even after \$(\d+) spend\.$/, (m) => `El ROAS de ${m[1]} está por debajo del punto de equilibrio tras $${m[2]} de gasto.`],
  [/^ROAS ([\d.]+) is marginal; cut budget 20% and test new creative\.$/, (m) => `El ROAS de ${m[1]} es justo; reduce el presupuesto un 20 % y prueba una creatividad nueva.`],
  [/^ROAS ([\d.]+) is strong; scale budget \+20%\.$/, (m) => `El ROAS de ${m[1]} es sólido; aumenta el presupuesto un 20 %.`],
  [/^CTR ([\d.]+)% is low; refresh headline\/creative in AI Studio\.$/, (m) => `El CTR de ${m[1]} % es bajo; renueva el titular o la creatividad en AI Studio.`],
  [/^(meta|tiktok|google) is not connected - add your credentials first$/, (m) => `${m[1]} no está conectado: agrega primero tus credenciales`],
  [/^Higgsfield error (\d+)$/, (m) => `Error de Higgsfield ${m[1]}`], [/^Meta error (\d+)$/, (m) => `Error de Meta ${m[1]}`],
  [/^TikTok error (\d+)$/, (m) => `Error de TikTok ${m[1]}`], [/^Google error (\d+)$/, (m) => `Error de Google ${m[1]}`], [/^WhatsApp error (\d+)$/, (m) => `Error de WhatsApp ${m[1]}`],
  [/^(\d+)s$/, (m) => `${m[1]} s`],
];

const clean = s => s.replace(/\s+/g, ' ');

/** Translate one string (no HTML handling); unknown strings are returned unchanged. */
export function translateText(text) {
  if (lang !== 'es' || !text) return text;
  const trimmed = text.trim();
  if (!trimmed) return text;
  const key = ES[trimmed] !== undefined ? trimmed : clean(trimmed);
  let out = ES[key];
  if (out === undefined) {
    for (const [re, fn] of PATTERNS) { const m = key.match(re); if (m) { out = fn(m); break; } }
  }
  if (out === undefined) return text;
  const lead = text.match(/^\s*/)[0], tail = text.match(/\s*$/)[0];
  return lead + out + tail;
}

/** t('Key with {name}', { name: value }): translated when on /es/, then {placeholders} are filled. */
export function t(key, vars) {
  let out = lang === 'es' && ES[key] !== undefined ? ES[key] : key;
  if (lang === 'es' && out === key) out = translateText(key);
  if (vars) out = out.replace(/\{(\w+)\}/g, (all, name) => (name in vars ? vars[name] : all));
  return out;
}

const ATTRS = ['placeholder', 'title', 'aria-label', 'alt'];
const NO_TEXT = 'script,style,textarea,code,pre,.msg,[data-no-i18n]';   // never translate inside these (user content, code)

function translateNode(node) {
  if (node.nodeType === 3) {
    const parent = node.parentElement;
    if (!parent || parent.closest(NO_TEXT)) return;
    const out = translateText(node.nodeValue);
    if (out !== node.nodeValue) node.nodeValue = out;
    return;
  }
  if (node.nodeType !== 1 || node.closest?.('.msg,[data-no-i18n]')) return;
  if (node.tagName === 'OPTION' && !node.hasAttribute('value')) node.setAttribute('value', node.textContent);   // keep the submitted value in English
  for (const a of ATTRS) {
    const v = node.getAttribute(a);
    if (v) { const out = translateText(v); if (out !== v) node.setAttribute(a, out); }
  }
  if (node.matches(NO_TEXT)) return;
  for (const child of node.childNodes) translateNode(child);
}

export function translateDom(root = document.body) { if (lang === 'es') translateNode(root); }

function setLanguageLinks() {
  const rest = appPath + location.search + location.hash;
  for (const l of ['en', 'es']) { const a = document.getElementById('lang-' + l); if (a) a.href = `/${l}${rest}`; }
  document.getElementById('lang-' + lang)?.setAttribute('aria-current', 'true');
  document.querySelectorAll('[data-lpath]').forEach(a => { a.href = L(a.dataset.lpath); });
}

/** Call once at startup: set <html lang>, title, language links, and keep translating whatever the app renders. */
export function startI18n() {
  document.documentElement.lang = lang;
  if (lang === 'es') document.title = 'youradds – Gestor de anuncios con IA';
  setLanguageLinks();
  addEventListener('hashchange', setLanguageLinks);
  if (lang !== 'es') return;
  translateDom(document.body);
  new MutationObserver(records => {
    for (const r of records) {
      if (r.type === 'childList') r.addedNodes.forEach(translateNode);
      else if (r.type === 'characterData') translateNode(r.target);
      else if (r.type === 'attributes') {
        const v = r.target.getAttribute(r.attributeName), out = v && translateText(v);
        if (out && out !== v) r.target.setAttribute(r.attributeName, out);
      }
    }
  }).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
}
