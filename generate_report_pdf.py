"""
Generate KSP IntelHub Project Report PDF
"""
from fpdf import FPDF
import os

class ReportPDF(FPDF):
    def __init__(self):
        super().__init__('P', 'mm', 'A4')
        self.set_auto_page_break(auto=True, margin=20)
        # Use built-in fonts only
        self.FONT = 'Helvetica'
        self.FONT_MONO = 'Courier'

    def header(self):
        if self.page_no() > 1:
            self.set_font(self.FONT, 'I', 8)
            self.set_text_color(120, 120, 120)
            self.cell(0, 8, 'KSP IntelHub - Project Report | Prepared for HOD Presentation', align='C')
            self.ln(4)
            self.set_draw_color(200, 200, 200)
            self.line(10, self.get_y(), 200, self.get_y())
            self.ln(6)

    def footer(self):
        self.set_y(-15)
        self.set_font(self.FONT, 'I', 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f'Page {self.page_no()}/{{nb}}', align='C')

    def add_cover(self):
        self.add_page()
        self.ln(50)
        # Title
        self.set_font(self.FONT, 'B', 32)
        self.set_text_color(20, 60, 120)
        self.cell(0, 16, 'KSP IntelHub', align='C', new_x="LMARGIN", new_y="NEXT")
        self.ln(4)
        self.set_font(self.FONT, '', 14)
        self.set_text_color(80, 80, 80)
        self.cell(0, 8, 'Karnataka State Police SCRB', align='C', new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 8, 'AI Intelligence Platform', align='C', new_x="LMARGIN", new_y="NEXT")
        self.ln(10)
        # Divider
        self.set_draw_color(20, 60, 120)
        self.set_line_width(0.8)
        self.line(60, self.get_y(), 150, self.get_y())
        self.ln(12)
        # Subtitle
        self.set_font(self.FONT, 'B', 12)
        self.set_text_color(60, 60, 60)
        self.cell(0, 8, 'Complete Project Report', align='C', new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 8, '11 ML Models | 26 Database Tables | Full Architecture', align='C', new_x="LMARGIN", new_y="NEXT")
        self.ln(20)
        self.set_font(self.FONT, '', 11)
        self.set_text_color(100, 100, 100)
        self.cell(0, 7, 'Prepared for: HOD Presentation', align='C', new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 7, 'Date: August 4, 2026', align='C', new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 7, 'Competition: KSP Datathon 2026', align='C', new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 7, 'GitHub: github.com/kartikbhartiya/Datathon', align='C', new_x="LMARGIN", new_y="NEXT")

    def section_title(self, num, title):
        self.ln(6)
        self.set_font(self.FONT, 'B', 16)
        self.set_text_color(20, 60, 120)
        self.cell(0, 10, f'{num}. {title}', new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(20, 60, 120)
        self.set_line_width(0.5)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(6)

    def sub_title(self, title):
        self.ln(3)
        self.set_font(self.FONT, 'B', 12)
        self.set_text_color(40, 80, 140)
        self.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def body(self, text):
        self.set_font(self.FONT, '', 10)
        self.set_text_color(40, 40, 40)
        self.set_x(10)
        self.multi_cell(190, 5.5, text)
        self.ln(2)

    def body_bold(self, text):
        self.set_font(self.FONT, 'B', 10)
        self.set_text_color(40, 40, 40)
        self.set_x(10)
        self.multi_cell(190, 5.5, text)
        self.ln(2)

    def bullet(self, text):
        self.set_font(self.FONT, '', 10)
        self.set_text_color(40, 40, 40)
        self.set_x(10)
        self.multi_cell(190, 5.5, '  -  ' + text)

    def code_block(self, text):
        self.ln(2)
        self.set_fill_color(240, 240, 245)
        self.set_font(self.FONT_MONO, '', 8.5)
        self.set_text_color(50, 50, 50)
        lines = text.strip().split('\n')
        x_start = self.get_x()
        for line in lines:
            self.cell(0, 4.8, '  ' + line, fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(3)

    def table_header(self, cols, widths):
        self.set_font(self.FONT, 'B', 9)
        self.set_fill_color(20, 60, 120)
        self.set_text_color(255, 255, 255)
        for i, col in enumerate(cols):
            self.cell(widths[i], 7, col, border=1, fill=True, align='C')
        self.ln()

    def table_row(self, cols, widths, fill=False):
        self.set_font(self.FONT, '', 8.5)
        self.set_text_color(40, 40, 40)
        if fill:
            self.set_fill_color(245, 248, 255)
        else:
            self.set_fill_color(255, 255, 255)
        max_h = 7
        # Calculate row height needed
        for i, col in enumerate(cols):
            lines_needed = max(1, len(str(col)) * self.get_string_width('a') / (widths[i] - 2) + 1)
        for i, col in enumerate(cols):
            self.cell(widths[i], max_h, str(col)[:int(widths[i]/1.8)], border=1, fill=fill)
        self.ln()

    def highlight_box(self, text, color='blue'):
        self.ln(2)
        if color == 'blue':
            self.set_fill_color(230, 240, 255)
            self.set_draw_color(20, 60, 120)
        elif color == 'green':
            self.set_fill_color(230, 255, 240)
            self.set_draw_color(20, 120, 60)
        elif color == 'orange':
            self.set_fill_color(255, 245, 230)
            self.set_draw_color(180, 120, 20)
        self.set_font(self.FONT, 'I', 9.5)
        self.set_text_color(40, 40, 40)
        self.set_line_width(0.3)
        self.set_x(10)
        y = self.get_y()
        self.multi_cell(190, 5.5, text, fill=True, border=1)
        self.set_x(10)
        self.ln(3)


def build_pdf():
    pdf = ReportPDF()
    pdf.alias_nb_pages()

    # ======== COVER PAGE ========
    pdf.add_cover()

    # ======== TABLE OF CONTENTS ========
    pdf.add_page()
    pdf.section_title('', 'Table of Contents')
    toc_items = [
        '1.  Project Overview & Problem Statement',
        '2.  System Architecture',
        '3.  Database Design - 26 Relational Tables',
        '4.  ML Model 1: Crime Volume Forecasting (Ridge + Fourier)',
        '5.  ML Model 2: Geospatial Crime Clustering (DBSCAN + Haversine)',
        '6.  ML Model 3: Co-Offender Link Prediction (Adamic-Adar)',
        '7.  ML Model 4: FIR Narrative Classifier (TF-IDF + Logistic Regression)',
        '8.  ML Model 5: Recidivism Risk Index (Multi-Factor Scoring)',
        '9.  ML Model 6: Case Allocation Predictor (Random Forest)',
        '10. ML Model 7: Suspect Conviction Predictor (Random Forest)',
        '11. ML Model 8: Crime Community Detection (Louvain Algorithm)',
        '12. ML Model 9: Suspect Recommendation Engine',
        '13. ML Model 10: Socio-Economic Crime Correlation (Pearson)',
        '14. ML Model 11: Suspect Crime Correlation Analysis',
        '15. AI Copilot - LLM Investigation Assistant',
        '16. Technology Stack Summary',
        '17. API Endpoints Reference',
        '18. Key Talking Points for the Presentation',
    ]
    for item in toc_items:
        pdf.set_font(pdf.FONT, '', 11)
        pdf.set_text_color(40, 40, 40)
        pdf.cell(0, 7, item, new_x="LMARGIN", new_y="NEXT")

    # ======== SECTION 1 ========
    pdf.add_page()
    pdf.section_title('1', 'Project Overview & Problem Statement')

    pdf.sub_title('What is KSP IntelHub?')
    pdf.body('KSP IntelHub is an AI-driven crime intelligence and analytical platform built for the State Crime Records Bureau (SCRB) of the Karnataka State Police (KSP). It replaces manual, siloed Excel-based crime reporting with an automated, real-time intelligence dashboard powered by machine learning.')
    pdf.body('Built for the KSP Datathon 2026, a competition organized by Karnataka State Police. The platform is designed for SCRB officers and District SPs to gain real-time situational awareness.')

    pdf.sub_title('The 4 Core Problems It Solves')
    pdf.body_bold('Problem 1: Data Silos & Manual Reporting')
    pdf.body('Solution: Unified 26 relational database tables (FIRs, Accused, Courts, Vehicles, Cybercrime, Officers, etc.) into a single queryable intelligence core.')
    pdf.body_bold('Problem 2: Lack of Advanced Analytics')
    pdf.body('Solution: Integrated 11 ML models - Ridge regression forecasting, DBSCAN clustering, Adamic-Adar link prediction, TF-IDF NLP classification, Random Forest predictors, Louvain community detection, and more.')
    pdf.body_bold('Problem 3: Reactive vs. Proactive Policing')
    pdf.body('Solution: Automated patrol dispatch recommendations based on predicted weekly crime spikes and high-risk DBSCAN zones.')
    pdf.body_bold('Problem 4: Opaque Criminal Networks')
    pdf.body('Solution: 3D force-directed network visualization with link prediction to uncover hidden co-offender alliances across Rowdy-Sheeters, cyber fraud rings, and stolen vehicle networks.')

    # ======== SECTION 2 ========
    pdf.add_page()
    pdf.section_title('2', 'System Architecture')

    pdf.sub_title('Architecture Overview')
    pdf.code_block(
        'USER (Browser)\n'
        '     |\n'
        '     v\n'
        'FRONTEND - Next.js 16 / React 19 (Port 3000)\n'
        '  Pages: Overview, Cases, Map, Network, Analytics,\n'
        '         Intel, Officers, Dispatch, Suspects, Courts,\n'
        '         Cyber, Vehicles, Missing Persons\n'
        '  Tech:  Tailwind CSS v4, Framer Motion, Recharts,\n'
        '         React Force Graph, Google Maps JS API\n'
        '     |\n'
        '     v  (REST API calls)\n'
        'BACKEND - Python Flask (Port 9000)\n'
        '  main.py       -> Flask routes, NetworkX graph engine\n'
        '  analytics.py  -> 11 ML models pipeline\n'
        '  Data: 26 CSV files loaded into Pandas DataFrames\n'
        '  Graph: NetworkX for relational network analysis\n'
        '  LLM: Groq (LLaMA 3.3 70B) / NVIDIA (MiniMax M3)\n'
        '     |\n'
        '     v\n'
        'DEPLOYMENT - Zoho Catalyst (AppSail Docker + Web Hosting)')

    pdf.sub_title('How Data Flows')
    pdf.bullet('Startup: Flask server loads all 26 CSV data files into Pandas DataFrames and builds a NetworkX graph connecting suspects, cases, officers, cyber IDs, and vehicles.')
    pdf.bullet('ML Training: On boot, the server trains all ML models (classifiers, forecasters) using the loaded data. Models are cached in memory.')
    pdf.bullet('API Request: Frontend makes REST calls -> Flask routes call analytics.py functions -> return JSON results.')
    pdf.bullet('AI Copilot: User queries go to /api/chat -> system dynamically searches all 26 tables in memory -> constructs context prompt -> sends to Groq/NVIDIA LLM -> returns intelligent response.')

    # ======== SECTION 3 ========
    pdf.add_page()
    pdf.section_title('3', 'Database Design - 26 Relational Tables')
    pdf.body('The platform integrates data from 26 tables/registries loaded from CSV files (simulating what would be a live SQL database in production):')

    tables_data = [
        ('CaseMaster', 'Core FIR/case records', 'CaseMasterID, CrimeNo, lat/lng, BriefFacts'),
        ('Accused', 'Accused persons per case', 'AccusedMasterID, CaseMasterID, PersonID'),
        ('Victim', 'Victims per case', 'VictimMasterID, CaseMasterID, VictimName'),
        ('CriminalsRegistry', 'Habitual offender DB', 'PersonID, gang, risk, rowdy_sheeter'),
        ('suspects', 'Suspect profiles', 'suspect_id, name, gang, risk_level'),
        ('case_links', 'Case-suspect relations', 'case_id, suspect_id, relationship'),
        ('case_reports', 'FIR narrative reports', 'case_id, report_text, officer_id'),
        ('CourtCasesRegistry', 'Court proceedings', 'CaseMasterID, conviction_status'),
        ('ChargesheetDetails', 'Chargesheet filings', 'CSID, CaseMasterID, csdate'),
        ('cybercrime', 'Digital fraud details', 'case_id, upi_ids, bank_accounts, fraud_amount'),
        ('stolen_vehicles', 'Vehicle theft registry', 'vehicle_number, recovery_status'),
        ('missing_persons', 'Missing persons', 'person_id, name, found_status'),
        ('OfficersRegistry', 'Officer performance', 'EmployeeID, closure_rate'),
        ('Employee', 'Officer details', 'EmployeeID, RankID, AppointmentDate'),
        ('complaints', 'Dial-112 logs', 'complaint_id, emergency_type'),
        ('cctv', 'CCTV feed metadata', 'camera_id, detected_faces/vehicle'),
        ('socioeconomic', 'District socio-eco data', 'district, unemployment, literacy'),
        ('Act/Section', 'Legal references', 'ActCode, SectionCode, Description'),
        ('ActSectionAssociation', 'Acts per case', 'CaseMasterID, ActID, SectionID'),
        ('ArrestSurrender', 'Arrest records', 'ArrestSurrenderID, CaseMasterID'),
        ('Court', 'Court master data', 'CourtID, CourtName, DistrictID'),
        ('CrimeHead/SubHead', 'Crime classification', 'Crime groups and sub-categories'),
        ('District/State', 'Geographic hierarchy', 'District and state master tables'),
        ('Designation/Rank', 'Officer rank hierarchy', 'RankName, Hierarchy'),
        ('Unit/UnitType', 'Police station org', 'Station types, hierarchy'),
    ]

    w = [42, 40, 108]
    pdf.table_header(['Table Name', 'Purpose', 'Key Fields'], w)
    for i, (name, purpose, fields) in enumerate(tables_data):
        pdf.table_row([name, purpose, fields], w, fill=(i % 2 == 0))

    pdf.ln(4)
    pdf.highlight_box('Terminology: A "Rowdy Sheeter" is a legal classification under Sections 95 & 96 of the Karnataka Police Act for habitual offenders involved in violence, extortion, or gang activities. It is a boolean flag in the CriminalsRegistry.', 'orange')

    # ======== SECTION 4: FORECASTING ========
    pdf.add_page()
    pdf.section_title('4', 'ML Model 1: Crime Volume Forecasting')
    pdf.sub_title('Algorithm: Ridge Regression + Fourier Seasonality Features')
    pdf.body('File: analytics.py -> _forecast_one() and build_forecast()')

    pdf.sub_title('What It Does')
    pdf.body('Predicts weekly crime incident counts up to 8 weeks into the future at three levels:')
    pdf.bullet('State-wide (all of Karnataka)')
    pdf.bullet('Per-district (each of Karnataka\'s districts)')
    pdf.bullet('Per crime type (Murder, Cyber Fraud, Vehicle Theft, etc.)')

    pdf.sub_title('Step-by-Step Workflow')
    pdf.code_block(
        'Raw FIR data (timestamps)\n'
        '  -> Group by week (Pandas resample "W")\n'
        '  -> Build feature matrix X:\n'
        '       Linear trend: t/n (normalised time index)\n'
        '       Fourier terms:\n'
        '         sin(2*pi*t/52), cos(2*pi*t/52) - annual cycle\n'
        '         sin(2*pi*t/26), cos(2*pi*t/26) - semi-annual\n'
        '         sin(2*pi*t/13), cos(2*pi*t/13) - quarterly\n'
        '  -> Fit RidgeCV (alphas: 0.1, 1.0, 5.0, 10.0, 50.0)\n'
        '  -> Compute residuals -> sigma (std deviation)\n'
        '  -> Predict next 8 weeks:\n'
        '       Point forecast = model.predict(future features)\n'
        '       Lower = forecast - 1.28*sigma (80% CI)\n'
        '       Upper = forecast + 1.28*sigma (80% CI)\n'
        '  -> Trend detection:\n'
        '       >6% increase -> "rising"\n'
        '       <-6% decrease -> "falling"\n'
        '       Otherwise -> "stable"')

    pdf.sub_title('Key Mathematical Concepts')
    pdf.body_bold('Fourier Series:')
    pdf.body('Any periodic function can be decomposed into sine and cosine waves. By using sin(2*pi*t/P) and cos(2*pi*t/P) for different periods P, we capture seasonal patterns in crime data without needing a dedicated time-series library.')
    pdf.body_bold('Ridge Regression:')
    pdf.body('Linear regression with L2 penalty (lambda * ||beta||^2) that shrinks coefficients to prevent overfitting. The cross-validated alpha selection (RidgeCV) automatically finds the best regularization strength from [0.1, 1.0, 5.0, 10.0, 50.0].')
    pdf.body_bold('Prediction Interval:')
    pdf.body('1.28*sigma corresponds to the 80th percentile of a normal distribution. +/-1.28 standard deviations covers approximately 80% of expected outcomes.')

    pdf.sub_title('Why Ridge + Fourier (Not ARIMA or Prophet)?')
    pdf.bullet('ARIMA requires stationarity testing and parameter tuning (p,d,q) which is complex for 30+ district-level series.')
    pdf.bullet('Prophet is heavy and slow for batch forecasting across many series.')
    pdf.bullet('Ridge + Fourier is lightweight, fast, and gives comparable accuracy for weekly aggregates.')
    pdf.bullet('Regularization prevents overfitting on small district-level datasets.')

    # ======== SECTION 5: DBSCAN ========
    pdf.add_page()
    pdf.section_title('5', 'ML Model 2: Geospatial Crime Clustering')
    pdf.sub_title('Algorithm: DBSCAN with Haversine Distance Metric')
    pdf.body('File: analytics.py -> build_geo_clusters()')

    pdf.sub_title('What It Does')
    pdf.body('Groups geographically dense crime incidents into bounded risk zones (clusters) on the map, identifying crime hotspots with center coordinates, radius in km, dominant crime type, average severity, and open case count.')

    pdf.sub_title('Step-by-Step Workflow')
    pdf.code_block(
        'Filter FIR data: severity_score >= 40 (high-severity only)\n'
        '  -> Convert lat/lng to radians\n'
        '  -> Run DBSCAN:\n'
        '       eps = 45km / 6371km (Earth radius) = 0.00707 radians\n'
        '       min_samples = 3\n'
        '       metric = "haversine"\n'
        '  -> For each cluster (label != -1 noise):\n'
        '       Centroid = mean(lat, lng)\n'
        '       Radius = 90th-percentile haversine distance\n'
        '       Dominant crime = mode(crime_type)\n'
        '       Avg severity = mean(severity_score)\n'
        '  -> Sort by case count, return top 20')

    pdf.sub_title('The Haversine Formula')
    pdf.code_block(
        'a = sin^2(delta_lat/2) + cos(lat1) * cos(lat2) * sin^2(delta_lng/2)\n'
        'distance = 2R * arcsin(sqrt(a))\n'
        'where R = 6371.0088 km (Earth\'s mean radius)')
    pdf.body('This is critical because Euclidean distance does not work for geographic coordinates. 1 degree of longitude varies in km depending on latitude.')

    pdf.sub_title('Why DBSCAN (Not K-Means)?')
    pdf.bullet('Does NOT require pre-specifying k (number of clusters) unlike K-Means.')
    pdf.bullet('Handles noise - isolated incidents are classified as noise (label = -1) and excluded.')
    pdf.bullet('Finds arbitrary-shaped clusters - crime zones are not always circular.')
    pdf.bullet('Works natively with haversine metric - essential for geographic data.')
    pdf.bullet('eps = 45 km chosen based on Karnataka district sizes for meaningful clusters.')

    # ======== SECTION 6: ADAMIC-ADAR ========
    pdf.add_page()
    pdf.section_title('6', 'ML Model 3: Co-Offender Link Prediction')
    pdf.sub_title('Algorithm: Adamic-Adar Index on Co-Offender Network Graph')
    pdf.body('File: analytics.py -> build_link_prediction()')

    pdf.sub_title('What It Does')
    pdf.body('Predicts hidden criminal alliances - identifies pairs of suspects who don\'t yet share a case but are likely connected based on shared network neighbors. "Who is likely to team up next for a crime?"')

    pdf.sub_title('Step-by-Step Workflow')
    pdf.code_block(
        'Step 1: Build undirected co-offender graph G\n'
        '  Nodes = all suspects\n'
        '  Edges: suspect A & B in same Case -> edge(A, B)\n'
        '  Edges: relationship = "Known Associate" -> edge(A, B)\n'
        '\n'
        'Step 2: Find candidate non-edges (friend-of-friend pairs)\n'
        '  For each node u -> neighbors of neighbors\n'
        '  If u and x NOT directly connected but share a neighbor\n'
        '  -> candidate pair\n'
        '\n'
        'Step 3: Score with Adamic-Adar Index\n'
        '  AA(u,v) = SUM over w in common_neighbors(u,v):\n'
        '              1 / log(degree(w))\n'
        '  Intuition: a shared associate who knows few people\n'
        '  is MORE significant than one who knows everyone\n'
        '\n'
        'Step 4: Rank by score, return top 12 predictions')

    pdf.sub_title('Why Adamic-Adar (Not Just Common Neighbors)?')
    pdf.body('Common Neighbors just counts shared connections. Adamic-Adar weights by rarity: a shared associate who only knows 2 people is far more indicative of a real connection than one who knows 200 people. Named after Lada Adamic and Eytan Adar (2003) for social network prediction.')

    pdf.sub_title('Real-World Use Case')
    pdf.highlight_box('Suspect A is part of a car theft ring. Suspect B is part of a different theft ring. They don\'t share any FIR cases. But both know Suspect C (a fence who sells stolen goods). Adamic-Adar scores this pair highly, alerting police to a potential emerging alliance.', 'green')

    # ======== SECTION 7: NLP CLASSIFIER ========
    pdf.add_page()
    pdf.section_title('7', 'ML Model 4: FIR Narrative Crime Classifier')
    pdf.sub_title('Algorithm: TF-IDF Vectorizer + Logistic Regression')
    pdf.body('File: analytics.py -> train_crime_classifier()')

    pdf.sub_title('What It Does')
    pdf.body('Takes the raw text of an FIR narrative (the "Brief Facts" written by police) and automatically classifies it into a crime category (Murder, Cyber Fraud, Vehicle Theft, etc.) with >90% validation accuracy.')

    pdf.sub_title('Step-by-Step Workflow')
    pdf.code_block(
        'Step 1: Merge case_reports with FIRs on case_id\n'
        'Step 2: TF-IDF Vectorization\n'
        '  stop_words = "english" (remove the, is, and, etc.)\n'
        '  ngram_range = (1,2) (single words AND 2-word phrases)\n'
        '  min_df = 3 (ignore words in < 3 documents)\n'
        '  max_features = 8000\n'
        'Step 3: 80/20 stratified train-test split\n'
        'Step 4: Logistic Regression (C=10.0, max_iter=1000)\n'
        'Step 5: Evaluate accuracy + macro F1-score\n'
        'Step 6: Refit on ALL data for production predictor\n'
        'Step 7: Extract top 8 keywords per class (Explainable AI)')

    pdf.sub_title('What is TF-IDF?')
    pdf.body('TF-IDF = Term Frequency x Inverse Document Frequency')
    pdf.code_block(
        'TF(t,d) = count(term t in doc d) / total_terms(d)\n'
        'IDF(t)  = log(total_docs / docs_containing_t)\n'
        'TF-IDF  = TF * IDF\n'
        '\n'
        'TF: How important is this word in THIS document?\n'
        'IDF: How rare is this word across ALL documents?\n'
        '"accused" appears in every FIR -> low IDF -> low score\n'
        '"snatched chain" appears only in snatching FIRs -> high score')

    pdf.sub_title('Why Logistic Regression (Not Deep Learning)?')
    pdf.bullet('Works extremely well with TF-IDF features')
    pdf.bullet('Interpretable: Can extract which words drive each prediction (Explainable AI)')
    pdf.bullet('Fast to train and predict (no GPU needed)')
    pdf.bullet('With C=10.0 and 8000 features, achieves >90% accuracy')

    pdf.sub_title('Explainability: Class Keywords Example')
    pdf.code_block(
        'Cyber Fraud: ["phishing","bank account","upi","fraud","otp"]\n'
        'Murder: ["stabbed","deceased","knife","blood","killed"]\n'
        'Vehicle Theft: ["vehicle","stolen","parked","registration"]')

    # ======== SECTION 8: RECIDIVISM ========
    pdf.add_page()
    pdf.section_title('8', 'ML Model 5: Recidivism Risk Index')
    pdf.sub_title('Algorithm: Transparent Multi-Factor Weighted Scoring (0-100)')
    pdf.body('File: analytics.py -> build_recidivism()')

    pdf.sub_title('What It Does')
    pdf.body('Calculates a recidivism risk score (0-100) for every offender, predicting their likelihood of re-offending. Classified into bands: Extreme (>=75), High (>=55), Elevated (>=35), Moderate (<35).')

    pdf.sub_title('The Scoring Formula')
    pdf.code_block(
        'Risk Score = (prior_offences * 30) + (prior_arrests * 20)\n'
        '           + (risk_level * 20) + (network_reach * 12)\n'
        '           + (active_warrant * 10) + (gang_ties * 8)\n'
        '                                          Total = 100')

    w = [45, 20, 125]
    pdf.table_header(['Factor', 'Weight', 'Calculation'], w)
    rows = [
        ('Prior Offences', '30', 'past_fir_count / 10 (capped at 10)'),
        ('Prior Arrests', '20', 'past_arrests / 8 (capped at 8)'),
        ('Risk Level', '20', 'Critical=1.0, High=0.75, Medium=0.45, Low=0.2'),
        ('Network Reach', '12', 'known_associates count / 6 (capped at 6)'),
        ('Active Warrant', '10', '1.0 if active warrant exists, else 0.0'),
        ('Gang Ties', '8', '1.0 if affiliated to any gang, else 0.0'),
    ]
    for i, r in enumerate(rows):
        pdf.table_row(r, w, fill=(i % 2 == 0))

    pdf.sub_title('Why Transparent Scoring (Not Black-Box ML)?')
    pdf.bullet('Legal defensibility: Police decisions must be explainable in court.')
    pdf.bullet('Auditability: Every officer can trace why a suspect scored 82/100.')
    pdf.bullet('No training data bias: Does not learn discriminatory patterns from historical data.')
    pdf.bullet('Domain expert alignment: Weights based on criminological research.')

    # ======== SECTION 9: ALLOCATION ========
    pdf.add_page()
    pdf.section_title('9', 'ML Model 6: Case Allocation Predictor')
    pdf.sub_title('Algorithm: Random Forest Classifier (100 trees, max_depth=8)')
    pdf.body('File: analytics.py -> train_allocation_predictor()')

    pdf.sub_title('What It Does')
    pdf.body('Predicts the probability of successful case closure if a specific officer is assigned to a specific case. Optimizes officer-case matching for maximum solve rates.')

    pdf.sub_title('Features Used (11 features)')
    w2 = [45, 30, 115]
    pdf.table_header(['Feature', 'Source', 'Description'], w2)
    feat_rows = [
        ('severity_score', 'Case', 'How severe the crime is (0-100)'),
        ('fatalities', 'Case', 'Number of deaths'),
        ('injured', 'Case', 'Number of injuries'),
        ('witnesses', 'Case', 'Number of witnesses'),
        ('closure_rate', 'Officer', 'Historical case closure rate (0-1)'),
        ('cases_assigned', 'Officer', 'Number of active cases'),
        ('commendations', 'Officer', 'Departmental commendations'),
        ('experience_years', 'Derived', 'Years since appointment date'),
        ('spec_match', 'Derived', '1.0 if specialization matches crime'),
        ('crime_encoded', 'Case', 'Label-encoded crime type'),
        ('rank_encoded', 'Officer', 'Label-encoded officer rank'),
    ]
    for i, r in enumerate(feat_rows):
        pdf.table_row(r, w2, fill=(i % 2 == 0))

    pdf.sub_title('Why Random Forest?')
    pdf.bullet('Handles mixed feature types (numerical + categorical).')
    pdf.bullet('Built-in feature importance ranking.')
    pdf.bullet('Robust to outliers; no feature scaling needed.')
    pdf.bullet('Ensemble of 100 trees gives stable predictions.')

    # ======== SECTION 10: CONVICTION ========
    pdf.add_page()
    pdf.section_title('10', 'ML Model 7: Suspect Conviction Predictor')
    pdf.sub_title('Algorithm: Random Forest Classifier (50 trees, max_depth=5)')
    pdf.body('File: analytics.py -> train_suspect_conviction_model()')

    pdf.sub_title('What It Does')
    pdf.body('Predicts the likelihood of conviction for an accused person based on their profile and criminal history. Helps prosecutors prioritize cases with strong evidence.')

    pdf.sub_title('Features Used (7 features)')
    pdf.bullet('age - Age of the accused')
    pdf.bullet('gender - Gender ID')
    pdf.bullet('past_firs - Number of prior FIR records')
    pdf.bullet('past_arrests - Number of prior arrests')
    pdf.bullet('rowdy_sheeter - Is this person a registered Rowdy Sheeter?')
    pdf.bullet('history_sheet - Does this person have an opened history sheet?')
    pdf.bullet('active_warrant - Is there an outstanding warrant?')

    pdf.sub_title('Training Process')
    pdf.body('1. Merge Accused table -> CriminalsRegistry -> CourtCasesRegistry')
    pdf.body('2. Filter to cases with known verdict: Convicted or Acquitted')
    pdf.body('3. Target variable: conviction_status == "Convicted" (binary 0/1)')
    pdf.body('4. Train Random Forest (50 trees, max_depth=5)')
    pdf.body('5. Output: Feature importances showing which factors predict conviction')

    # ======== SECTION 11: LOUVAIN ========
    pdf.section_title('11', 'ML Model 8: Crime Community Detection')
    pdf.sub_title('Algorithm: Louvain Algorithm (Community Detection)')
    pdf.body('File: analytics.py -> detect_crime_communities()')

    pdf.sub_title('What It Does')
    pdf.body('Automatically detects organized crime cells/syndicates by finding densely connected communities in the co-offender network graph. Reveals hidden gang structures not explicitly recorded.')

    pdf.sub_title('How It Works')
    pdf.code_block(
        'Step 1: Build undirected graph from edges where\n'
        '        relationship in {Co-Offender, Charged, Known Associate}\n'
        'Step 2: Apply Louvain community detection\n'
        '        Maximizes modularity Q (density within vs between)\n'
        '        Fallback: Label Propagation if Louvain fails\n'
        'Step 3: Filter communities >= 3 members\n'
        'Step 4: Score community risk:\n'
        '        Low=1, Medium=2, High=3, Critical=4\n'
        '        >=3.2 = Critical, >=2.5 = High, >=1.5 = Medium\n'
        'Step 5: Return syndicates with IDs, size, members, gangs')

    pdf.body('The Louvain algorithm is a greedy optimization algorithm running in O(n log n) time. It maximizes modularity Q - a measure of how much the network structure deviates from a random network. Proposed by Blondel et al. (2008).')

    # ======== SECTION 12: SUSPECT RECOMMENDATION ========
    pdf.add_page()
    pdf.section_title('12', 'ML Model 9: Suspect Recommendation Engine')
    pdf.sub_title('Algorithm: Multi-Factor Scoring (MO + Spatial + Risk)')
    pdf.body('File: analytics.py -> recommend_suspects_for_case()')

    pdf.sub_title('What It Does')
    pdf.body('Given an unsolved case, recommends the top 5 most likely suspects based on three scoring dimensions:')

    pdf.body_bold('1. Modus Operandi (MO) Similarity - Max 40 points')
    pdf.bullet('Matching crime type history: +20 points')
    pdf.bullet('Matching weapon used: +10 points')
    pdf.bullet('Matching vehicle used: +5 points')
    pdf.bullet('Matching target type: +5 points')

    pdf.body_bold('2. Spatiotemporal Proximity - Max 35 points')
    pdf.bullet('Operated in same district: +15 points')
    pdf.bullet('Crime scene < 5 km from past activity: +5 to +20 (inverse distance)')
    pdf.bullet('Crime scene < 15 km from past activity: +10 points')

    pdf.body_bold('3. Offender Risk Profile - Max 25 points')
    pdf.bullet('Flagged as Rowdy Sheeter: +8 points')
    pdf.bullet('Active arrest warrant: +8 points')
    pdf.bullet('Opened history sheet: +4 points')
    pdf.bullet('Past arrests (x1.5 per arrest, max 5): up to +5 points')

    pdf.body('Spatial proximity uses the Haversine formula to calculate minimum distance between the crime scene and all of the suspect\'s past locations.')

    # ======== SECTION 13: SOCIO-ECONOMIC ========
    pdf.add_page()
    pdf.section_title('13', 'ML Model 10: Socio-Economic Crime Correlation')
    pdf.sub_title('Algorithm: Pearson Correlation Analysis')
    pdf.body('File: analytics.py -> build_socioeconomic_correlation()')

    pdf.sub_title('What It Does')
    pdf.body('Correlates district-level crime patterns with socio-economic indicators. Answers the "why behind the where" - why certain districts have more crime.')

    pdf.sub_title('Correlation Pairs Analyzed')
    w3 = [50, 50, 90]
    pdf.table_header(['Socio-Economic Factor', 'Crime Metric', 'Question'], w3)
    corr_rows = [
        ('Unemployment', 'Property-crime %', 'Does unemployment drive theft?'),
        ('Unemployment', 'Crime rate/100k', 'Does unemployment drive crime?'),
        ('Urbanisation', 'Cyber-fraud %', 'Are urban areas more cyber-prone?'),
        ('Literacy', 'Cyber-fraud %', 'Do literate areas see more fraud?'),
        ('Per-capita income', 'Crime rate/100k', 'Does income reduce crime?'),
        ('Police/100k', 'Crime rate/100k', 'Does more policing reduce crime?'),
        ('Slum population', 'Property-crime %', 'Do slums drive property crime?'),
        ('Youth share', 'Crime rate/100k', 'Does younger population = more?'),
    ]
    for i, r in enumerate(corr_rows):
        pdf.table_row(r, w3, fill=(i % 2 == 0))

    pdf.sub_title('Pearson Correlation Coefficient')
    pdf.code_block(
        'r = SUM((xi - x_mean)(yi - y_mean)) /\n'
        '    (sqrt(SUM(xi - x_mean)^2) * sqrt(SUM(yi - y_mean)^2))\n'
        '\n'
        '|r| >= 0.6  -> "strong"\n'
        '|r| >= 0.35 -> "moderate"\n'
        '|r| <  0.35 -> "weak"\n'
        'r > 0  -> positive (both increase together)\n'
        'r < 0  -> negative (one up, other down)')

    # ======== SECTION 14: SUSPECT CORRELATION ========
    pdf.section_title('14', 'ML Model 11: Suspect Crime Correlation')
    pdf.sub_title('Algorithm: Cosine Similarity + Z-Score Deviance')
    pdf.body('File: analytics.py -> get_suspect_crime_correlation()')

    pdf.sub_title('What It Does')
    pdf.body('Generates a detailed criminological profile for a single suspect:')
    pdf.bullet('Temporal Correlation: Cosine similarity between suspect\'s time-of-day distribution and system-wide distribution.')
    pdf.bullet('Socio-Economic Profile: Weighted average of indicators across suspect\'s active districts.')
    pdf.bullet('Severity Deviance: Z-score = (suspect_avg - system_avg) / system_std.')
    pdf.bullet('Crime Specialization: >70% in one type = "High Specialist", <40% = "Generalist".')
    pdf.bullet('Co-Offender Network: Who does this suspect work with?')

    # ======== SECTION 15: AI COPILOT ========
    pdf.add_page()
    pdf.section_title('15', 'AI Copilot - LLM Investigation Assistant')
    pdf.sub_title('Model: Groq LLaMA 3.3 70B / NVIDIA MiniMax M3')
    pdf.body('Endpoint: /serverless/api/chat (POST)')

    pdf.sub_title('What It Does')
    pdf.body('A natural language investigation assistant allowing officers to ask complex questions in English or Kannada:')
    pdf.bullet('"Who are the top Rowdy Sheeters in Bengaluru with active warrants?"')
    pdf.bullet('"Show me all cyber fraud cases with fraud amount > 50 lakhs"')
    pdf.bullet('"Which districts saw a spike in vehicle thefts this quarter?"')

    pdf.sub_title('Architecture (RAG-like)')
    pdf.code_block(
        'User Query (text or voice)\n'
        '  -> Dynamic Memory Search:\n'
        '       Search all 26 in-memory DataFrames\n'
        '       Filter relevant rows by query keywords\n'
        '  -> Construct System Prompt:\n'
        '       Database schema + matched records + domain terms\n'
        '  -> Send to LLM (Groq / NVIDIA API)\n'
        '       Model: llama-3.3-70b-versatile\n'
        '  -> Return natural language response\n'
        '  -> Optional: Smart routing (e.g., /network?q=name)')

    pdf.sub_title('Why RAG-like (Not Fine-Tuned)?')
    pdf.bullet('No training data needed - works with any new dataset immediately.')
    pdf.bullet('Always up-to-date - queries live data, not a static model.')
    pdf.bullet('Flexible - handles questions never explicitly trained on.')
    pdf.bullet('Cost-effective - uses API calls, no GPU infrastructure.')

    # ======== SECTION 16: TECH STACK ========
    pdf.add_page()
    pdf.section_title('16', 'Technology Stack Summary')

    pdf.sub_title('Backend (Python Flask API)')
    w4 = [45, 30, 115]
    pdf.table_header(['Library', 'Version', 'Purpose'], w4)
    be_rows = [
        ('Flask', '3.0.3', 'REST API framework'),
        ('Pandas', '2.2.2', 'Data loading, manipulation, aggregation'),
        ('NumPy', '1.26.4', 'Numerical computations'),
        ('Scikit-learn', '1.4.2', 'ML: Ridge, DBSCAN, LogReg, RF, TF-IDF'),
        ('NetworkX', '3.3', 'Graph: Adamic-Adar, Louvain community'),
        ('OpenAI', '1.40.0', 'LLM API client (Groq/NVIDIA endpoints)'),
        ('Gunicorn', '22.0.0', 'Production WSGI server'),
    ]
    for i, r in enumerate(be_rows):
        pdf.table_row(r, w4, fill=(i % 2 == 0))

    pdf.ln(4)
    pdf.sub_title('Frontend (Next.js Dashboard)')
    pdf.table_header(['Library', 'Version', 'Purpose'], w4)
    fe_rows = [
        ('Next.js', '16.2.9', 'React framework with App Router'),
        ('React', '19.2.4', 'UI component library'),
        ('Tailwind CSS', '4.x', 'Utility-first CSS framework'),
        ('Framer Motion', '12.40.0', 'Animations and transitions'),
        ('Recharts', '3.8.1', 'Charts and data visualization'),
        ('Force Graph 2D/3D', '1.29.1', 'Network graph visualization'),
        ('Three.js', '0.184.0', '3D rendering for network graphs'),
        ('Google Maps API', '-', 'Geospatial heatmaps and markers'),
    ]
    for i, r in enumerate(fe_rows):
        pdf.table_row(r, w4, fill=(i % 2 == 0))

    # ======== SECTION 17: API ========
    pdf.ln(6)
    pdf.section_title('17', 'API Endpoints Reference')
    w5 = [18, 72, 100]
    pdf.table_header(['Method', 'Endpoint', 'Description'], w5)
    api_rows = [
        ('GET', '/serverless/api/health', 'System health, record counts, graph stats'),
        ('GET', '/serverless/api/stats', 'KPI aggregations & crime trends'),
        ('GET', '/serverless/api/cases', 'Paginated FIR list with search/filters'),
        ('GET', '/serverless/api/network', 'Network graph nodes + edges'),
        ('GET', '/serverless/api/heatmap', 'Spatial coords, KDE, DBSCAN zones'),
        ('GET', '/serverless/api/analytics', 'Full ML predictions (all models)'),
        ('POST', '/serverless/api/chat', 'AI Copilot NL query engine'),
        ('POST', '/serverless/api/classify', 'FIR narrative crime classifier'),
    ]
    for i, r in enumerate(api_rows):
        pdf.table_row(r, w5, fill=(i % 2 == 0))

    # ======== SECTION 18: TALKING POINTS ========
    pdf.add_page()
    pdf.section_title('18', 'Key Talking Points for the Presentation')

    pdf.sub_title('Opening (2 minutes)')
    pdf.highlight_box('"KSP IntelHub is an AI-powered crime intelligence platform for Karnataka State Police that replaces manual Excel-based reporting with real-time ML-driven analytics across 26 integrated data registries."', 'blue')

    pdf.sub_title('The ML Story (5-7 minutes)')
    pdf.body_bold('Walk through these 8 key points:')
    pdf.bullet('"We forecast crime" - Ridge + Fourier gives 8-week predictions with 80% confidence intervals')
    pdf.bullet('"We find crime zones" - DBSCAN clusters geo-located incidents using Earth\'s curvature (Haversine)')
    pdf.bullet('"We predict hidden alliances" - Adamic-Adar on the co-offender NetworkX graph')
    pdf.bullet('"We auto-classify FIRs" - TF-IDF + Logistic Regression at >90% accuracy with explainable keywords')
    pdf.bullet('"We score recidivism risk" - Transparent 100-point weighted formula (legally defensible)')
    pdf.bullet('"We detect crime syndicates" - Louvain community detection finds hidden gang structures')
    pdf.bullet('"We recommend suspects" - Multi-factor scoring with MO matching and spatial proximity')
    pdf.bullet('"We correlate crime with socio-economics" - Pearson analysis reveals why certain districts are hotspots')

    pdf.sub_title('If HOD Asks Technical Questions')
    pdf.body_bold('Why Ridge over ARIMA?')
    pdf.body('Fourier features capture seasonality without stationarity requirements; Ridge\'s L2 regularization handles small district-level datasets.')
    pdf.body_bold('Why DBSCAN over K-Means?')
    pdf.body('No need to specify k; handles noise; works with haversine metric.')
    pdf.body_bold('Why Logistic Regression over Neural Networks?')
    pdf.body('Interpretable (extractable keywords); fast; >90% accuracy is sufficient for this use case.')
    pdf.body_bold('Why scoring formula for recidivism?')
    pdf.body('Legal defensibility and auditability; no black-box bias risks.')
    pdf.body_bold('Graph Analytics:')
    pdf.body('NetworkX provides O(n log n) Louvain community detection and efficient Adamic-Adar computation.')

    pdf.sub_title('Closing (1 minute)')
    pdf.highlight_box('"The platform is deployed on Zoho Catalyst, integrates 11 ML models, processes 26 data registries, and gives Karnataka Police officers proactive intelligence instead of reactive reports."', 'green')

    # Save
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'KSP_IntelHub_Project_Report.pdf')
    pdf.output(out_path)
    print(f"\n{'='*60}")
    print(f"PDF generated successfully!")
    print(f"Location: {out_path}")
    print(f"{'='*60}")
    return out_path


if __name__ == '__main__':
    build_pdf()
