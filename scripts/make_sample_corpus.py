"""Generate a small synthetic corpus (3 PDFs) and a matching evaluation set.

Why: lets you run the whole pipeline + evaluation in minutes, before you collect
your own PDFs. The facts are fictional ("Acme"), so any correct answer proves
retrieval worked rather than the LLM's memory.

    python -m scripts.make_sample_corpus
Writes:  data/sample_docs/*.pdf   and   eval/questions.sample.json
"""

import json
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "sample_docs"

FILLER = [
    "This section applies to all permanent employees of Acme Technologies unless stated otherwise.",
    "Managers are expected to apply these rules consistently and to document any exception.",
    "Questions about this policy should be directed to the Human Resources or IT service desk.",
]

# Each fact: (sentence in the document, question, keyword that must appear in a correct answer)
DOCS = {
    "Acme_Leave_Policy.pdf": [
        (
            "Annual Leave",
            [
                (
                    "Full-time employees receive 24 days of paid annual leave per calendar year.",
                    "How many days of paid annual leave do full-time employees get each year?",
                    "24 days",
                ),
                (
                    "Unused annual leave of up to 10 days may be carried forward into the next year.",
                    "How many unused annual leave days can be carried forward?",
                    "10 days",
                ),
                (
                    "Leave requests longer than 5 consecutive days must be submitted at least 14 days in advance.",
                    "How far in advance must a leave request longer than 5 consecutive days be submitted?",
                    "14 days",
                ),
                (
                    "The direct manager approves or declines a leave request within 3 working days.",
                    "Within how many working days does the manager decide on a leave request?",
                    "3 working days",
                ),
            ],
        ),
        (
            "Sick and Parental Leave",
            [
                (
                    "Employees may take up to 12 days of paid sick leave per year.",
                    "How many days of paid sick leave can employees take per year?",
                    "12 days",
                ),
                (
                    "A medical certificate is required when sick leave exceeds 2 consecutive days.",
                    "When is a medical certificate required for sick leave?",
                    "2 consecutive",
                ),
                (
                    "Maternity leave is 26 weeks of fully paid leave.",
                    "How long is maternity leave?",
                    "26 weeks",
                ),
                (
                    "Paternity leave is 10 working days and must be taken within 6 months of the birth.",
                    "How long is paternity leave?",
                    "10 working days",
                ),
            ],
        ),
        (
            "Other Leave",
            [
                (
                    "Bereavement leave is 5 days for the death of an immediate family member.",
                    "How many days of bereavement leave are given for an immediate family member?",
                    "5 days",
                ),
                (
                    "Employees receive 2 days of paid volunteering leave per year to support a registered charity.",
                    "How many days of paid volunteering leave do employees get per year?",
                    "2 days",
                ),
                (
                    "An unpaid career break of up to 6 months may be requested after 3 years of service.",
                    "After how many years of service can an employee request an unpaid career break?",
                    "3 years",
                ),
                (
                    "Employees are entitled to 7 days of paid marriage leave.",
                    "How many days of paid marriage leave are employees entitled to?",
                    "7 days",
                ),
            ],
        ),
    ],
    "Acme_IT_Security_Policy.pdf": [
        (
            "Passwords and Access",
            [
                (
                    "Passwords must contain at least 14 characters.",
                    "What is the minimum password length?",
                    "14 characters",
                ),
                (
                    "Passwords must be changed every 90 days.",
                    "How often must passwords be changed?",
                    "90 days",
                ),
                (
                    "After 5 failed login attempts the account is locked for 30 minutes.",
                    "How long is an account locked after repeated failed login attempts?",
                    "30 minutes",
                ),
                (
                    "Multi-factor authentication is mandatory for all remote access and for the finance system.",
                    "For which kinds of access is multi-factor authentication mandatory?",
                    "remote access",
                ),
            ],
        ),
        (
            "Devices and Data",
            [
                (
                    "Company laptops must be encrypted with BitLocker or FileVault.",
                    "Which tools are used to encrypt company laptops?",
                    "BitLocker",
                ),
                (
                    "Lost or stolen devices must be reported to the IT helpdesk within 2 hours.",
                    "Within how long must a lost or stolen device be reported?",
                    "2 hours",
                ),
                (
                    "USB storage devices are blocked by default and exceptions need written approval from the CISO.",
                    "Who must approve exceptions for USB storage devices?",
                    "CISO",
                ),
                (
                    "Customer data may only be stored on the approved cloud platform called SecureVault.",
                    "Which platform is approved for storing customer data?",
                    "SecureVault",
                ),
            ],
        ),
        (
            "Incidents and Training",
            [
                (
                    "Suspected phishing emails must be forwarded to security-reports@acme.example.",
                    "Where should suspected phishing emails be forwarded?",
                    "security-reports@acme.example",
                ),
                (
                    "Security incidents are graded from P1 (critical) to P4 (low).",
                    "What severity levels are used to grade security incidents?",
                    "P4",
                ),
                (
                    "All employees must complete security awareness training every 12 months.",
                    "How often must employees complete security awareness training?",
                    "12 months",
                ),
                (
                    "Data backups are retained for 35 days.",
                    "How long are data backups retained?",
                    "35 days",
                ),
            ],
        ),
    ],
    "Acme_Travel_Expense_Policy.pdf": [
        (
            "Booking Travel",
            [
                (
                    "Flights shorter than 4 hours must be booked in economy class.",
                    "In which class must flights shorter than 4 hours be booked?",
                    "economy",
                ),
                (
                    "International trips must be booked at least 21 days before departure.",
                    "How far in advance must international trips be booked?",
                    "21 days",
                ),
                (
                    "Hotel stays are capped at 8,000 rupees per night in metro cities.",
                    "What is the nightly hotel cap in metro cities?",
                    "8,000",
                ),
                (
                    "All travel must be booked through the Acme TravelDesk portal.",
                    "Through which portal must travel be booked?",
                    "TravelDesk",
                ),
            ],
        ),
        (
            "Claiming Expenses",
            [
                (
                    "The daily meal allowance is 1,200 rupees when travelling domestically.",
                    "What is the daily meal allowance for domestic travel?",
                    "1,200",
                ),
                (
                    "Expense claims must be submitted within 30 days of returning from the trip.",
                    "Within how many days of returning must expense claims be submitted?",
                    "30 days",
                ),
                (
                    "Receipts are mandatory for any single expense above 500 rupees.",
                    "Above what amount are receipts mandatory for a single expense?",
                    "500",
                ),
                (
                    "Reimbursement is paid within 10 working days of claim approval.",
                    "How soon is reimbursement paid after a claim is approved?",
                    "10 working days",
                ),
            ],
        ),
        (
            "Local Travel and Upgrades",
            [
                (
                    "Personal car use is reimbursed at 12 rupees per kilometre.",
                    "What is the reimbursement rate for personal car use?",
                    "12 rupees",
                ),
                (
                    "Airport taxis are reimbursed up to 1,500 rupees per trip.",
                    "What is the maximum reimbursement for airport taxis per trip?",
                    "1,500",
                ),
                (
                    "Business class is allowed only on flights longer than 8 hours and needs VP approval.",
                    "When is business class allowed?",
                    "8 hours",
                ),
                (
                    "Alcohol is not reimbursable under any circumstances.",
                    "Is alcohol reimbursable?",
                    "not reimbursable",
                ),
            ],
        ),
    ],
}

UNANSWERABLE = [
    "What is the stock option vesting schedule?",
    "How many days per week can employees work from home?",
    "What is the dress code policy?",
    "What is the salary band for a senior engineer?",
    "Who is the CEO of Acme Technologies?",
    "What relocation assistance does Acme provide?",
    "How much is the annual performance bonus?",
    "What is on the cafeteria menu on Fridays?",
    "What is the notice period when an employee resigns?",
    "Does Acme provide health insurance for employees' parents?",
    "What is the policy on using personal phones for work calls?",
    "How many public holidays does Acme observe each year?",
    # near-miss questions: the topic IS in the documents, but this exact fact is not
    "What is the daily meal allowance for international travel?",
    "How many days of paid adoption leave do employees get?",
    "What is the hotel cap per night in non-metro cities?",
    "How long are email archives retained?",
    "How often must contractors change their passwords?",
    "What is the reimbursement rate for motorbike use?",
]


def build_pdf(name, sections):
    styles = getSampleStyleSheet()
    story = []
    for i, (heading, facts) in enumerate(sections):
        if i:
            story.append(PageBreak())
        story.append(
            Paragraph(
                f"{name.replace('_', ' ')[:-4]} - Section {i + 1}: {heading}", styles["Heading2"]
            )
        )
        story.append(Spacer(1, 12))
        for j, (sentence, _, _) in enumerate(facts):
            story.append(Paragraph(sentence + " " + FILLER[j % len(FILLER)], styles["BodyText"]))
            story.append(Spacer(1, 8))
    SimpleDocTemplate(str(OUT / name), pagesize=A4).build(story)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    questions = []
    for name, sections in DOCS.items():
        build_pdf(name, sections)
        for page, (_, facts) in enumerate(sections, start=1):
            for _, question, keyword in facts:
                questions.append(
                    {
                        "question": question,
                        "answerable": True,
                        "source": name,
                        "page": page,
                        "keywords": [keyword],
                    }
                )
    questions += [{"question": q, "answerable": False} for q in UNANSWERABLE]
    (ROOT / "eval" / "questions.sample.json").write_text(
        json.dumps(questions, indent=2), encoding="utf-8"
    )
    print(
        f"Wrote {len(DOCS)} PDFs to {OUT} and {len(questions)} questions "
        f"({len(questions) - len(UNANSWERABLE)} answerable, {len(UNANSWERABLE)} unanswerable)."
    )


if __name__ == "__main__":
    main()
