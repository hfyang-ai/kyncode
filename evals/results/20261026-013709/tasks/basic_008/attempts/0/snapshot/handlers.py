import smtplib


def send_email(to, subject, body):
    # connect to SMTP and dispatch an email message
    smtp = smtplib.SMTP('localhost')
    smtp.sendmail('noreply@x.com', to, f'{subject}\n{body}')
