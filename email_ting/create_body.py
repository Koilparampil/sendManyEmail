import os
import tkinter as tk
from tkinter import simpledialog

def create_email_subject_body(chosen: dict) -> tuple[str, str]:
    """Create the body of the email based on the chosen carrier ID info."""
    if chosen['booking_number'] is not None and chosen['booking_number'] != "":
        subject = f"NY TITLES - {chosen['booking_number']}"
    else:
        subject = "NY TITLES - NO BOOKING NUMBER"    
    if (chosen['customer_name'] != "No Records Found") & (chosen['customer_name'] != None):
        body = f"FROM {chosen['customer_name']} \n"
        match chosen:
            case count if count["Titles"] != 0:
                body += f"{chosen['Titles']} TITLES "
                if count['Salvage Certificates'] != 0:
                    body += f"{chosen['Salvage Certificates']} SALVAGE CERTIFICATES"
            case count if (count["Titles"] == 0 and count["Salvage Certificates"] != 0):
                body += f"{chosen['Salvage Certificates']} SALVAGE CERTIFICATES "
            case count if count["Titles"] == 0 and count["Salvage Certificates"] == 0:
                if chosen["Bills of Sale"] != 0:
                    body += f"{chosen['Bills of Sale']} BILLS OF SALE "
                else:
                    if chosen["Other Documents"] != 0:
                        body += f"{chosen['Other Documents']} OTHER DOCUMENTS "
            case _:
                body += "NO DOCUMENT COUNT INFO"                    
    else:
        root = tk.Tk()
        root.withdraw()
        name = simpledialog.askstring("Customer Name Not Found", "Enter the customer name manually:", parent=root)
        root.destroy()
        body = f"FROM {name} \n"
        match chosen:
            case count if count["Titles"] != 0:
                body += f"{chosen['Titles']} TITLES "
                if count['Salvage Certificates'] != 0:
                    body += f"{chosen['Salvage Certificates']} SALVAGE CERTIFICATES"
            case count if count["Titles"] == 0 and count["Salvage Certificates"] != 0:
                body += f"{chosen['Salvage Certificates']} SALVAGE CERTIFICATES "
            case count if count["Titles"] == 0 and count["Salvage Certificates"] == 0:
                if chosen["Bills of Sale"] != 0:
                    body += f"{chosen['Bills of Sale']} BILLS OF SALE "
                else:
                    if chosen["Other Documents"] != 0:
                        body += f"{chosen['Other Documents']} OTHER DOCUMENTS "
            case _:
                body += "NO DOCUMENT COUNT INFO"
    body += f"\n\n\n\nThanks/Regards\n{os.getlogin()}"
    return subject , body


if __name__ == "__main__":
    try:
        subjecct, body = create_email_subject_body({"booking_number": "12345", "customer_name": "John Doe", "Titles": 1, "Salvage Certificates": 4, "Bills of Sale": 0, "Other Documents": 1})
        print(body)
    except Exception as e:
        print(f"An error occurred while sending the email. {e}")