from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from math import gcd

from crm_api.models import Account, Lead, Contact, Opportunity

# Sample data for realistic CRM data
INDUSTRIES = [
    "Technology",
    "Healthcare",
    "Finance",
    "Manufacturing",
    "Retail",
    "Education",
    "Real Estate",
    "Consulting",
    "Media",
    "Automotive",
    "Energy",
    "Telecommunications",
    "Transportation",
    "Food & Beverage",
    "Pharmaceuticals",
    "Insurance",
    "Legal",
    "Construction",
    "Agriculture",
    "Aerospace",
    "Banking",
    "Biotechnology",
    "Chemicals",
    "Defense",
    "Entertainment",
    "Fashion",
    "Gaming",
    "Hospitality",
    "Logistics",
]

REGIONS = ["North America", "Europe", "Asia Pacific", "Latin America", "Middle East & Africa"]

LEAD_SOURCES = [
    "Website",
    "Referral",
    "Cold Call",
    "Email Campaign",
    "Social Media",
    "Trade Show",
    "Partner",
    "LinkedIn",
    "Google Ads",
    "Content Marketing",
]

LEAD_STATUSES = ["new", "contacted", "qualified", "proposal", "negotiation", "closed-won", "closed-lost"]

OPPORTUNITY_STAGES = ["prospecting", "qualification", "proposal", "negotiation", "closed-won", "closed-lost"]

JOB_TITLES = [
    "CEO",
    "CTO",
    "VP Sales",
    "VP Marketing",
    "Director of Operations",
    "Sales Manager",
    "Marketing Manager",
    "Product Manager",
    "Account Manager",
    "Business Analyst",
    "Project Manager",
    "HR Director",
    "Finance Director",
    "Chief Technology Officer",
    "Chief Marketing Officer",
    "Chief Financial Officer",
    "VP Engineering",
    "VP Product",
    "VP Customer Success",
    "Regional Manager",
    "Territory Manager",
    "Account Executive",
    "Sales Director",
    "Marketing Director",
    "Operations Director",
    "IT Director",
    "Procurement Manager",
    "Supply Chain Manager",
]

DEPARTMENTS = [
    "Sales",
    "Marketing",
    "Engineering",
    "Operations",
    "Finance",
    "HR",
    "Customer Success",
    "IT",
    "Legal",
    "Procurement",
]

# Predefined company names
COMPANY_NAMES = [
    "Acme Corporation",
    "Tech Solutions Inc",
    "Global Dynamics",
    "Innovation Labs",
    "Future Systems",
    "Digital Partners",
    "Cloud Technologies",
    "Data Analytics Co",
    "Smart Solutions",
    "NextGen Corp",
    "Alpha Industries",
    "Beta Systems",
    "Gamma Technologies",
    "Delta Solutions",
    "Epsilon Corp",
    "Zeta Enterprises",
    "Eta Industries",
    "Theta Systems",
    "Iota Technologies",
    "Kappa Solutions",
    "Lambda Corp",
    "Mu Industries",
    "Nu Systems",
    "Xi Technologies",
    "Omicron Solutions",
    "Pi Enterprises",
    "Rho Industries",
    "Sigma Systems",
    "Tau Technologies",
    "Upsilon Corp",
    "Phi Industries",
    "Chi Systems",
    "Psi Technologies",
    "Omega Solutions",
    "Alpha Beta Corp",
    "Gamma Delta Inc",
    "Epsilon Zeta LLC",
    "Eta Theta Corp",
    "Iota Kappa Inc",
    "Lambda Mu LLC",
    "Nu Xi Corp",
    "Omicron Pi Inc",
    "Rho Sigma LLC",
    "Tau Upsilon Corp",
    "Phi Chi Inc",
    "Psi Omega LLC",
    "Advanced Systems",
    "Premier Technologies",
    "Elite Solutions",
    "Superior Corp",
    "Excellence Inc",
    "Quality Systems",
    "Reliable Technologies",
    "Trusted Solutions",
    "Proven Corp",
    "Established Inc",
    "Renowned Systems",
    "Famous Technologies",
    "Popular Solutions",
    "Leading Corp",
    "Top Industries",
    "Best Systems",
    "Prime Technologies",
    "First Solutions",
    "Number One Corp",
    "Apex Industries",
    "Summit Systems",
    "Peak Technologies",
    "Crest Solutions",
    "Crown Corp",
    "Royal Industries",
    "Noble Systems",
    "Prestigious Technologies",
    "Distinguished Solutions",
    "Honored Corp",
    "Respected Inc",
    "Admired Systems",
    "Valued Technologies",
    "Cherished Solutions",
    "Beloved Corp",
    "Preferred Inc",
    "Chosen Systems",
    "Selected Technologies",
    "Picked Solutions",
    "Chosen Corp",
    "Elected Inc",
    "Voted Systems",
    "Approved Technologies",
    "Accepted Solutions",
    "Agreed Corp",
    "Consented Inc",
    "Approved Systems",
    "Endorsed Technologies",
    "Supported Solutions",
    "Backed Corp",
    "Funded Inc",
    "Invested Systems",
    "Capitalized Technologies",
    "Financed Solutions",
    "Monetized Corp",
    "Profitable Inc",
    "Lucrative Systems",
    "Rewarding Technologies",
    "Beneficial Solutions",
    "Advantageous Corp",
    "Favorable Inc",
    "Positive Systems",
    "Constructive Technologies",
    "Productive Solutions",
    "Effective Corp",
    "Efficient Inc",
    "Streamlined Systems",
    "Optimized Technologies",
    "Enhanced Solutions",
    "Improved Corp",
    "Upgraded Inc",
    "Modernized Systems",
    "Updated Technologies",
    "Refreshed Solutions",
    "Renewed Corp",
    "Revitalized Inc",
    "Rejuvenated Systems",
    "Reinvigorated Technologies",
    "Reenergized Solutions",
    "Recharged Corp",
]

# Predefined first names
FIRST_NAMES = [
    "John",
    "Jane",
    "Michael",
    "Sarah",
    "David",
    "Lisa",
    "Robert",
    "Jennifer",
    "William",
    "Jessica",
    "James",
    "Ashley",
    "Christopher",
    "Amanda",
    "Daniel",
    "Stephanie",
    "Matthew",
    "Melissa",
    "Anthony",
    "Nicole",
    "Mark",
    "Elizabeth",
    "Donald",
    "Helen",
    "Steven",
    "Deborah",
    "Paul",
    "Dorothy",
    "Andrew",
    "Lisa",
    "Joshua",
    "Nancy",
    "Kenneth",
    "Karen",
    "Kevin",
    "Betty",
    "Brian",
    "Sandra",
    "George",
    "Donna",
    "Timothy",
    "Carol",
    "Ronald",
    "Ruth",
    "Jason",
    "Sharon",
    "Edward",
    "Michelle",
    "Jeffrey",
    "Laura",
    "Ryan",
    "Sarah",
    "Jacob",
    "Kimberly",
    "Gary",
    "Deborah",
    "Nicholas",
    "Dorothy",
    "Eric",
    "Lisa",
    "Jonathan",
    "Nancy",
    "Stephen",
    "Karen",
    "Larry",
    "Betty",
    "Justin",
    "Helen",
    "Scott",
    "Sandra",
    "Brandon",
    "Donna",
    "Benjamin",
    "Carol",
    "Samuel",
    "Ruth",
    "Gregory",
    "Sharon",
    "Alexander",
    "Michelle",
    "Patrick",
    "Laura",
    "Jack",
    "Sarah",
    "Dennis",
    "Kimberly",
    "Jerry",
    "Deborah",
    "Tyler",
    "Dorothy",
    "Aaron",
    "Lisa",
    "Jose",
    "Nancy",
    "Henry",
    "Karen",
    "Adam",
    "Betty",
    "Douglas",
    "Helen",
    "Nathan",
    "Sandra",
    "Peter",
    "Donna",
    "Zachary",
    "Carol",
    "Kyle",
    "Ruth",
    "Noah",
    "Sharon",
    "Alan",
    "Michelle",
    "Ethan",
    "Laura",
    "Jeremy",
    "Sarah",
    "Keith",
    "Kimberly",
    "Christian",
    "Deborah",
    "Austin",
    "Dorothy",
    "Sean",
    "Lisa",
    "Gerald",
    "Nancy",
    "Carl",
    "Karen",
    "Harold",
    "Betty",
    "Wayne",
    "Helen",
    "Arthur",
    "Sandra",
    "Terry",
    "Donna",
    "Lawrence",
    "Carol",
    "Joe",
    "Ruth",
    "Eugene",
    "Sharon",
    "Ralph",
    "Michelle",
    "Bobby",
    "Laura",
    "Louis",
    "Sarah",
    "Philip",
    "Kimberly",
    "Johnny",
    "Deborah",
    "Roy",
    "Dorothy",
    "Roger",
    "Lisa",
    "Howard",
    "Nancy",
    "Juan",
    "Karen",
    "Albert",
    "Betty",
    "Willie",
    "Helen",
    "Elmer",
    "Sandra",
    "Wayne",
    "Donna",
    "Eugene",
    "Carol",
]

# Predefined last names
LAST_NAMES = [
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Garcia",
    "Miller",
    "Davis",
    "Rodriguez",
    "Martinez",
    "Hernandez",
    "Lopez",
    "Gonzalez",
    "Wilson",
    "Anderson",
    "Thomas",
    "Taylor",
    "Moore",
    "Jackson",
    "Martin",
    "Lee",
    "Perez",
    "Thompson",
    "White",
    "Harris",
    "Sanchez",
    "Clark",
    "Ramirez",
    "Lewis",
    "Robinson",
    "Walker",
    "Young",
    "Allen",
    "King",
    "Wright",
    "Scott",
    "Torres",
    "Nguyen",
    "Hill",
    "Flores",
    "Green",
    "Adams",
    "Nelson",
    "Baker",
    "Hall",
    "Rivera",
    "Campbell",
    "Mitchell",
    "Carter",
    "Roberts",
    "Gomez",
    "Phillips",
    "Evans",
    "Turner",
    "Diaz",
    "Parker",
    "Cruz",
    "Edwards",
    "Collins",
    "Reyes",
    "Stewart",
    "Morris",
    "Morales",
    "Murphy",
    "Cook",
    "Rogers",
    "Gutierrez",
    "Ortiz",
    "Morgan",
    "Cooper",
    "Peterson",
    "Bailey",
    "Reed",
    "Kelly",
    "Howard",
    "Ramos",
    "Kim",
    "Cox",
    "Ward",
    "Richardson",
    "Watson",
    "Brooks",
    "Chavez",
    "Wood",
    "James",
    "Bennett",
    "Gray",
    "Mendoza",
    "Ruiz",
    "Hughes",
    "Price",
    "Alvarez",
    "Castillo",
    "Sanders",
    "Patel",
    "Myers",
    "Long",
    "Ross",
    "Foster",
    "Jimenez",
    "Powell",
    "Jenkins",
    "Perry",
    "Russell",
    "Sullivan",
    "Bell",
    "Coleman",
    "Butler",
    "Henderson",
    "Barnes",
    "Gonzales",
    "Fisher",
    "Vasquez",
    "Simmons",
    "Romero",
    "Jordan",
    "Patterson",
    "Alexander",
    "Hamilton",
    "Graham",
    "Reynolds",
    "Griffin",
    "Wallace",
    "Moreno",
    "West",
    "Cole",
    "Hayes",
    "Bryant",
    "Herrera",
    "Gibson",
    "Ellis",
    "Tran",
    "Medina",
    "Aguilar",
    "Stevens",
    "Murray",
    "Ford",
    "Castro",
    "Marshall",
    "Owens",
    "Harrison",
    "Fernandez",
    "McDonald",
    "Woods",
    "Washington",
    "Kennedy",
    "Wells",
    "Vargas",
    "Henry",
    "Chen",
    "Freeman",
    "Webb",
    "Tucker",
    "Guzman",
    "Burns",
    "Crawford",
    "Olson",
    "Simpson",
    "Porter",
    "Hunter",
    "Gordon",
    "Mendez",
    "Silva",
    "Shaw",
    "Snyder",
    "Mason",
    "Dixon",
    "Munoz",
    "Hunt",
    "Hicks",
    "Holmes",
    "Palmer",
    "Wagner",
    "Black",
    "Robertson",
    "Boyd",
    "Rose",
    "Stone",
    "Salazar",
    "Fox",
]

# Predefined email domains
EMAIL_DOMAINS = [
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "company.com",
    "business.org",
    "enterprise.net",
    "corporate.com",
    "firm.org",
    "group.com",
    "team.net",
    "office.org",
    "workplace.com",
    "professional.net",
    "executive.org",
    "management.com",
    "leadership.net",
    "strategy.org",
    "operations.com",
    "development.net",
    "innovation.org",
    "technology.com",
    "solutions.net",
    "services.org",
    "consulting.com",
    "advisory.net",
    "partners.org",
    "associates.com",
    "ventures.net",
    "holdings.org",
    "enterprises.com",
    "industries.net",
    "systems.org",
    "technologies.com",
    "solutions.net",
    "services.org",
    "consulting.com",
]

# Predefined cities
CITIES = [
    "New York",
    "Los Angeles",
    "Chicago",
    "Houston",
    "Phoenix",
    "Philadelphia",
    "San Antonio",
    "San Diego",
    "Dallas",
    "San Jose",
    "Austin",
    "Jacksonville",
    "Fort Worth",
    "Columbus",
    "Charlotte",
    "San Francisco",
    "Indianapolis",
    "Seattle",
    "Denver",
    "Washington",
    "Boston",
    "El Paso",
    "Nashville",
    "Detroit",
    "Oklahoma City",
    "Portland",
    "Las Vegas",
    "Memphis",
    "Louisville",
    "Baltimore",
    "Milwaukee",
    "Albuquerque",
    "Tucson",
    "Fresno",
    "Sacramento",
    "Mesa",
    "Kansas City",
    "Atlanta",
    "Long Beach",
    "Colorado Springs",
    "Raleigh",
    "Miami",
    "Virginia Beach",
    "Omaha",
    "Oakland",
    "Minneapolis",
    "Tulsa",
    "Arlington",
    "Tampa",
    "New Orleans",
    "Wichita",
    "Cleveland",
    "Bakersfield",
    "Aurora",
    "Anaheim",
    "Honolulu",
    "Santa Ana",
    "Corpus Christi",
    "Riverside",
    "Lexington",
    "Stockton",
    "Henderson",
    "Saint Paul",
    "St. Louis",
    "Milwaukee",
    "Milwaukee",
    "Milwaukee",
    "Milwaukee",
    "Milwaukee",
    "Milwaukee",
    "Milwaukee",
]

# Predefined states
STATES = [
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "Florida",
    "Georgia",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "West Virginia",
    "Wisconsin",
    "Wyoming",
]

# Predefined countries
COUNTRIES = [
    "United States",
    "Canada",
    "Mexico",
    "United Kingdom",
    "Germany",
    "France",
    "Italy",
    "Spain",
    "Netherlands",
    "Sweden",
    "Norway",
    "Denmark",
    "Finland",
    "Switzerland",
    "Austria",
    "Belgium",
    "Ireland",
    "Portugal",
    "Greece",
    "Poland",
    "Czech Republic",
    "Hungary",
    "Slovakia",
    "Slovenia",
    "Croatia",
    "Romania",
    "Bulgaria",
    "Estonia",
    "Latvia",
    "Lithuania",
    "Japan",
    "South Korea",
    "China",
    "India",
    "Australia",
    "New Zealand",
    "Singapore",
    "Hong Kong",
    "Taiwan",
    "Thailand",
    "Malaysia",
    "Indonesia",
    "Philippines",
    "Vietnam",
    "Brazil",
    "Argentina",
    "Chile",
    "Colombia",
    "Peru",
    "Venezuela",
    "Uruguay",
    "Paraguay",
    "Bolivia",
    "Ecuador",
    "Guyana",
    "Suriname",
]

# Predefined opportunity names
OPPORTUNITY_NAMES = [
    "Enterprise Software License",
    "Cloud Migration Project",
    "Digital Transformation Initiative",
    "CRM Implementation",
    "ERP System Upgrade",
    "Data Analytics Platform",
    "AI/ML Solutions",
    "Cybersecurity Services",
    "IT Infrastructure Modernization",
    "Mobile App Development",
    "Website Redesign",
    "E-commerce Platform",
    "Marketing Automation",
    "Sales Enablement Tools",
    "Customer Support System",
    "HR Management Platform",
    "Financial Management Software",
    "Supply Chain Optimization",
    "Inventory Management System",
    "Business Intelligence Dashboard",
    "API Integration Services",
    "Database Migration",
    "Network Security Audit",
    "Compliance Consulting",
    "Training and Development",
    "Process Optimization",
    "Quality Assurance Services",
    "Project Management Tools",
    "Communication Platform",
    "Collaboration Software",
    "Document Management System",
    "Workflow Automation",
    "Performance Monitoring",
    "Backup and Recovery",
    "Disaster Recovery Plan",
    "Business Continuity",
    "Risk Assessment",
    "Security Audit",
    "Penetration Testing",
    "Vulnerability Assessment",
    "Compliance Review",
    "Regulatory Consulting",
    "Legal Advisory",
    "Financial Planning",
    "Investment Analysis",
    "Market Research",
    "Competitive Analysis",
    "Strategic Planning",
    "Change Management",
    "Organizational Development",
    "Leadership Training",
    "Team Building",
    "Product Development",
    "Market Entry Strategy",
    "International Expansion",
    "Partnership Development",
    "Merger and Acquisition",
    "Due Diligence",
    "Valuation Services",
    "Transaction Support",
    "Post-Merger Integration",
    "Divestiture Support",
    "Restructuring Advisory",
    "Turnaround Services",
    "Interim Management",
    "Executive Search",
    "Talent Acquisition",
    "Succession Planning",
    "Performance Management",
    "Employee Engagement",
    "Culture Transformation",
    "Diversity and Inclusion",
    "Sustainability Consulting",
    "Environmental Compliance",
    "Carbon Footprint Analysis",
    "Green Technology",
    "Renewable Energy",
    "Energy Efficiency",
    "Waste Reduction",
    "Water Management",
    "Air Quality",
    "Noise Control",
    "Environmental Impact Assessment",
    "Sustainability Reporting",
    "ESG Consulting",
]

# Predefined opportunity descriptions
OPPORTUNITY_DESCRIPTIONS = [
    "Comprehensive solution for enterprise needs",
    "Scalable platform for business growth",
    "Integrated system for improved efficiency",
    "Advanced technology for competitive advantage",
    "Streamlined processes for better productivity",
    "Robust infrastructure for reliability",
    "User-friendly interface for enhanced experience",
    "Secure environment for data protection",
    "Flexible configuration for customization",
    "Cost-effective solution for budget optimization",
    "Future-proof technology for long-term value",
    "Proven methodology for guaranteed results",
    "Expert support for seamless implementation",
    "Comprehensive training for user adoption",
    "Ongoing maintenance for continuous operation",
    "Regular updates for latest features",
    "24/7 support for critical operations",
    "Dedicated team for project success",
    "Best practices for industry standards",
    "Innovative approach for breakthrough results",
    "Collaborative process for stakeholder alignment",
    "Transparent communication for project clarity",
    "Risk mitigation for project security",
    "Quality assurance for deliverable excellence",
    "Timeline management for on-time delivery",
    "Budget control for cost predictability",
    "Change management for smooth transition",
    "Knowledge transfer for sustainability",
    "Documentation for future reference",
    "Lessons learned for continuous improvement",
]


def permuted_revenue(
    account_id: int,
    min_value: int = 100_000,
    max_value: int = 10_000_000,
    step: int = 10_000,
    a_hint: int = 1_234_567,
    b: int = 9_876,
) -> int:
    """
    Deterministic permutation for annual revenue that generates 1000 unique values.
    - Returns values aligned to 'step' (10k increments)
    - Same account_id always produces the same revenue value
    """
    if step <= 0:
        raise ValueError("step must be positive")

    # Align to the step
    min_aligned = ((min_value + step - 1) // step) * step
    max_aligned = (max_value // step) * step
    if min_aligned > max_aligned:
        raise ValueError("Range too narrow after alignment to 'step'.")

    # Number of distinct revenue buckets
    K = (max_aligned - min_aligned) // step + 1

    # Map ID into [0, K-1]
    x = account_id % K

    # Pick 'a' coprime with K (so the affine map is a permutation)
    a = a_hint
    while gcd(a, K) != 1:
        a += 1

    # Affine permutation
    rank = (a * x + b) % K

    # Map back to the value space
    return min_aligned + rank * step


def permuted_value_1k(
    opportunity_id: int,
    min_value: int = 5_000,
    max_value: int = 500_000,
    step: int = 1_000,
    a_hint: int = 1_664_525,  # seed for 'a'
    b: int = 12_345,
) -> int:
    """
    Deterministic permutation over K thousand-buckets so the last 3 digits are 000.
    - Adjusts min/max to multiples of 'step'.
    - Uses an affine permutation modulo K: rank = (a*x + b) % K with gcd(a, K) == 1.
    - Returns: min_aligned + rank*step
    """
    if step <= 0:
        raise ValueError("step must be positive")

    # Align to the step (1,000) so values end with 000
    min_aligned = ((min_value + step - 1) // step) * step
    max_aligned = (max_value // step) * step
    if min_aligned > max_aligned:
        raise ValueError("Range too narrow after alignment to 'step'.")

    # Number of distinct thousand-buckets
    K = (max_aligned - min_aligned) // step + 1

    # Map ID into [0, K-1]
    x = opportunity_id % K

    # Pick 'a' coprime with K (so the affine map is a permutation)
    a = a_hint
    # Nudge 'a' until gcd(a, K) == 1
    while gcd(a, K) != 1:
        a += 1

    # Affine permutation
    rank = (a * x + b) % K

    # Map back to the value space (always ends with 000)
    return min_aligned + rank * step


def generate_accounts(count: int = 1000):
    """Generate sample accounts using predefined arrays"""
    accounts = []
    for i in range(count):
        # Cycle through arrays to ensure variety
        company_name = COMPANY_NAMES[i % len(COMPANY_NAMES)]
        industry = INDUSTRIES[i % len(INDUSTRIES)]
        city = CITIES[i % len(CITIES)]
        state = STATES[i % len(STATES)]
        country = COUNTRIES[i % len(COUNTRIES)]
        region = REGIONS[i % len(REGIONS)]

        # Generate email based on company name
        # email_domain = EMAIL_DOMAINS[i % len(EMAIL_DOMAINS)]
        # email = f"contact@{company_name.lower().replace(' ', '').replace('.', '').replace(',', '')}.{email_domain}"

        # Generate phone number
        phone = f"+1-{555:03d}-{1000 + (i % 9000):04d}"

        # Generate website
        website = f"https://www.{company_name.lower().replace(' ', '').replace('.', '').replace(',', '')}.com"

        # Generate address
        address = f"{100 + (i % 9000)} {CITIES[i % len(CITIES)]} Street"

        # Generate revenue using deterministic permutation (1000 unique values)
        annual_revenue = permuted_revenue(i, min_value=100_000, max_value=10_000_000, step=10_000)
        employee_count = 10 + (i % 100) * 50  # 10 to 5000

        account = Account(
            name=company_name,
            industry=industry,
            website=website,
            phone=phone,
            # email=email,
            address=address,
            city=city,
            state=state,
            country=country,
            region=region,
            annual_revenue=annual_revenue,
            employee_count=employee_count,
        )
        accounts.append(account)
    return accounts


def generate_leads(count: int = 2000):
    """Generate sample leads using predefined arrays"""
    leads = []
    for i in range(count):
        first_name = FIRST_NAMES[i % len(FIRST_NAMES)]
        last_name = LAST_NAMES[i % len(LAST_NAMES)]
        company = COMPANY_NAMES[i % len(COMPANY_NAMES)]
        job_title = JOB_TITLES[i % len(JOB_TITLES)]
        industry = INDUSTRIES[i % len(INDUSTRIES)]
        source = LEAD_SOURCES[i % len(LEAD_SOURCES)]
        status = LEAD_STATUSES[i % len(LEAD_STATUSES)]
        email_domain = EMAIL_DOMAINS[i % len(EMAIL_DOMAINS)]

        # Generate email
        email = f"{first_name.lower()}.{last_name.lower()}@{email_domain}"

        # Generate phone
        phone = f"+1-{555:03d}-{2000 + (i % 8000):04d}"

        # Generate score based on status
        score_map = {
            "new": 20,
            "contacted": 40,
            "qualified": 60,
            "proposal": 80,
            "negotiation": 90,
            "closed-won": 100,
            "closed-lost": 0,
        }
        score = score_map.get(status, 20)

        # Generate notes
        notes = f"Lead from {source} for {industry} industry. Contact at {company} as {job_title}."

        lead = Lead(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            company=company,
            job_title=job_title,
            industry=industry,
            source=source,
            status=status,
            score=score,
            notes=notes,
        )
        leads.append(lead)
    return leads


def generate_contacts(accounts: list, contacts_per_account: int = 5):
    """Generate sample contacts for accounts using predefined arrays"""
    contacts = []
    contact_id = 0
    for account in accounts:
        # Generate 3-8 contacts per account
        num_contacts = 1  # 3 + (contact_id % 6)
        for i in range(num_contacts):
            first_name = FIRST_NAMES[contact_id % len(FIRST_NAMES)]
            last_name = LAST_NAMES[contact_id % len(LAST_NAMES)]
            job_title = JOB_TITLES[contact_id % len(JOB_TITLES)]
            department = DEPARTMENTS[contact_id % len(DEPARTMENTS)]
            email_domain = EMAIL_DOMAINS[contact_id % len(EMAIL_DOMAINS)]

            # Generate email
            email = f"{first_name.lower()}.{last_name.lower()}@{account.name.lower().replace(' ', '').replace('.', '').replace(',', '')}.{email_domain}"

            # Generate phone
            phone = f"+1-{555:03d}-{3000 + (contact_id % 7000):04d}"

            contact = Contact(
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=phone,
                job_title=job_title,
                department=department,
                is_primary=(i == 0),  # First contact is primary
                account_id=account.id,
            )
            contacts.append(contact)
            contact_id += 1
    return contacts


def generate_opportunities(accounts: list, opportunities_per_account: int = 8):
    """Generate sample opportunities for accounts using predefined arrays"""
    opportunities = []
    opportunity_id = 0
    for idx, account in enumerate(accounts):
        if idx % 5 != 0:
            continue
        # Generate 2-10 opportunities per account
        num_opportunities = 2  # + (opportunity_id % 9)
        for i in range(num_opportunities):
            opportunity_name = OPPORTUNITY_NAMES[opportunity_id % len(OPPORTUNITY_NAMES)]
            description = OPPORTUNITY_DESCRIPTIONS[opportunity_id % len(OPPORTUNITY_DESCRIPTIONS)]
            stage = OPPORTUNITY_STAGES[opportunity_id % len(OPPORTUNITY_STAGES)]

            # Generate close date within next 12 months
            days_ahead = 30 + (opportunity_id % 365)
            close_date = datetime.now() + timedelta(days=days_ahead)

            # Generate value with some variation
            # base_value = 5000 + (opportunity_id % 50) * 1000  # 5k to 50k base
            # multiplier = 1 + (opportunity_id % 10)  # 1x to 10x multiplier
            value = permuted_value_1k(opportunity_id)

            # Generate probability based on stage
            prob_map = {
                "prospecting": 0.1,
                "qualification": 0.3,
                "proposal": 0.6,
                "negotiation": 0.8,
                "closed-won": 1.0,
                "closed-lost": 0.0,
            }
            probability = prob_map.get(stage, 0.5)

            opportunity = Opportunity(
                name=f"{account.name} - {opportunity_name}",
                description=description,
                value=value,
                currency="USD",
                stage=stage,
                probability=probability,
                close_date=close_date,
                account_id=account.id,
            )
            opportunities.append(opportunity)
            opportunity_id += 1
    return opportunities


def seed_database(db: Session):
    """Seed the database with sample data"""
    print("Seeding database with sample data...")

    # Generate accounts (1000)
    print("Generating 1000 accounts...")
    accounts = generate_accounts(1000)
    db.add_all(accounts)
    db.commit()
    db.refresh(accounts[0])  # Refresh to get IDs

    # Generate leads (2000)
    print("Generating 2000 leads...")
    leads = generate_leads(2000)
    db.add_all(leads)
    db.commit()

    # Generate contacts for accounts (5000+)
    print("Generating contacts...")
    contacts = generate_contacts(accounts)
    db.add_all(contacts)
    db.commit()

    # Generate opportunities for accounts (8000+)
    print("Generating opportunities...")
    opportunities = generate_opportunities(accounts)
    db.add_all(opportunities)
    db.commit()

    print("Database seeded successfully!")
    print(f"- {len(accounts)} accounts")
    print(f"- {len(leads)} leads")
    print(f"- {len(contacts)} contacts")
    print(f"- {len(opportunities)} opportunities")
    print(f"- Total records: {len(accounts) + len(leads) + len(contacts) + len(opportunities)}")
