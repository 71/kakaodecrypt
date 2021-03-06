#!/usr/bin/python3

from Crypto.Cipher import AES
import hashlib
import base64

class KakaoDecrypt:
  key_cache = {}

  @staticmethod
  def genSalt(user_id, encType):
    if user_id <= 0:
      return b'\0'*16

    prefixes = ['','','12','24','18','30','36','12','48','7','35','40','17','23','29',
                'isabel','kale','sulli','van','merry','kyle','james', 'maddux',
                'tony', 'hayden', 'paul', 'elijah']
    try:
      salt = prefixes[encType] + str(user_id)
    except IndexError:
      raise ValueError('Unsupported encoding type %i' % encType)
    salt = salt + '\0' * (16 - len(salt))
    return salt.encode('UTF-8')

  @staticmethod
  def pkcs16adjust(a, aOff, b):
     x = (b[len(b) - 1] & 0xff) + (a[aOff + len(b) - 1] & 0xff) + 1
     a[aOff + len(b) - 1] = x % 256
     x = x >> 8;
     for i in range(len(b)-2, -1, -1):
       x = x + (b[i] & 0xff) + (a[aOff + i] & 0xff)
       a[aOff + i] = x % 256
       x = x >> 8

  # PKCS12 key derivation as implemented in Bouncy Castle (using SHA1).
  # See org/bouncycastle/crypto/generators/PKCS12ParametersGenerator.java.
  @staticmethod
  def deriveKey(password, salt, iterations, dkeySize):
    password = (password + b'\0').decode('ascii').encode('utf-16-be')

    hasher = hashlib.sha1()
    v = hasher.block_size
    u = hasher.digest_size

    D = [ 1 ] * v
    S = [ 0 ] * v * int((len(salt) + v - 1) / v)
    for i in range(0, len(S)):
      S[i] = salt[i % len(salt)]
    P = [ 0 ] * v * int((len(password) + v - 1) / v)
    for i in range(0, len(P)):
      P[i] = password[i % len(password)]

    I = S + P

    B = [ 0 ] * v
    c = int((dkeySize + u - 1) / u)

    dKey = [0] * dkeySize
    for i in range(1, c+1):
      hasher = hashlib.sha1()
      hasher.update(bytes(D))
      hasher.update(bytes(I))
      A = hasher.digest()

      for j in range(1, iterations):
        hasher = hashlib.sha1()
        hasher.update(A)
        A = hasher.digest()

      A = list(A)
      for j in range(0, len(B)):
        B[j] = A[j % len(A)]

      for j in range(0, int(len(I)/v)):
        KakaoDecrypt.pkcs16adjust(I, j * v, B)

      start = (i - 1) * u
      if i == c:
        dKey[start : dkeySize] = A[0 : dkeySize-start]
      else:
        dKey[start : start+len(A)] = A[0 : len(A)]

    return bytes(dKey)

  @staticmethod
  def decrypt(user_id, encType, b64_ciphertext):
    key = b'\x16\x08\x09\x6f\x02\x17\x2b\x08\x21\x21\x0a\x10\x03\x03\x07\x06'
    iv = b'\x0f\x08\x01\x00\x19\x47\x25\xdc\x15\xf5\x17\xe0\xe1\x15\x0c\x35'

    salt = KakaoDecrypt.genSalt(user_id, encType)
    if salt in KakaoDecrypt.key_cache:
      key = KakaoDecrypt.key_cache[salt]
    else:
      key = KakaoDecrypt.deriveKey(key, salt, 2, 32)
      KakaoDecrypt.key_cache[salt] = key
    encoder = AES.new(key, AES.MODE_CBC, iv)

    ciphertext = base64.b64decode(b64_ciphertext)
    if len(ciphertext) == 0:
      return b64_ciphertext
    padded = encoder.decrypt(ciphertext)
    try:
      plaintext = padded[:-padded[-1]]
    except IndexError:
      raise ValueError('Unable to decrypt data', ciphertext)
    try:
      return plaintext.decode('UTF-8')
    except UnicodeDecodeError:
      return plaintext

class KakaoDbDecrypt:
  @staticmethod
  def copy_table_struct(cur, from_table, to_table):
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='%s'" % from_table)
    new_tbl_stmt = cur.fetchone()[0]
    new_tbl_stmt = new_tbl_stmt.replace(from_table, to_table, 1)
    cur.execute("DROP TABLE IF EXISTS %s" % to_table)
    cur.execute(new_tbl_stmt)

  @staticmethod
  def run(db_file, enc_table, dec_table, enc_fields, do_print):
    import sqlite3
    import json

    con = sqlite3.connect(db_file)
    cur = con.cursor()

    cur.execute('PRAGMA table_info(%s)' % enc_table)
    rows = cur.fetchall()
    if len(rows) == 0:
      return
    col_defs = { row[1]: row[0] for row in rows }

    if not do_print:
      KakaoDbDecrypt.copy_table_struct(cur, enc_table, dec_table)

    if enc_table != 'chat_logs':
      cur.execute('SELECT user_id FROM open_profile LIMIT 1')
      profile_id = cur.fetchone()[0]
    else:
      profile_id = None

    if do_print:
      print("-- Table '%s'" % enc_table)
    cur.execute('SELECT * FROM %s' % enc_table)
    rows = cur.fetchall()

    for row in rows:
      try:
        enc_type = row[ col_defs['enc'] ]
      except KeyError:
        v = row[ col_defs['v'] ]
        v_data = json.loads(v)
        enc_type = v_data['enc']

      if profile_id is None:
        user_id = row[ col_defs['user_id'] ]
      else:
        user_id = profile_id

      decrypted_row = list(row)

      for enc_col in enc_fields:
        contents = row[ col_defs[enc_col] ]
        if contents is not None:
          contents = KakaoDecrypt.decrypt(user_id, enc_type, contents)
        decrypted_row[ col_defs[enc_col] ] = contents

      if do_print:
        print('|'.join([str(col) for col in decrypted_row]))
      else:
        values = ','.join(['?'] * len(decrypted_row))
        cur.execute("INSERT INTO %s values (%s)" % (dec_table, values), decrypted_row)

    if not do_print:
      con.commit()
      print("Created table '%s'." % dec_table)

if __name__ == '__main__':
  import sys
  do_print = False
  dec_suffix = '_dec'
  enc_schema = {
    'chat_logs': ['message', 'attachment'],
    'friends':   ['uuid', 'phone_number', 'raw_phone_number', 'name',
                  'profile_image_url', 'full_profile_image_url',
                  'original_profile_image_url', 'status_message', 'v',
                  'board_v', 'ext', 'nick_name', 'contact_name'],
    'friends_board_contents' : [ 'image_url', 'thumbnail_url', 'url', 'v' ],
  }

  try:
    if sys.argv[1] == '-p':
      do_print = True
      db_file = sys.argv[2]
    else:
      db_file = sys.argv[1]
  except IndexError:
    print('Usage: %s [-p] KakaoTalk[2].db' % sys.argv[0])
    print("Decrypt contents of tables into new tables suffixed with %s." % dec_suffix)
    print('-p  Print decrypted table contents to stdout instead')
    sys.exit()

  for enc_table, enc_fields in enc_schema.items():
    dec_table = enc_table + dec_suffix
    KakaoDbDecrypt.run(db_file, enc_table, dec_table, enc_fields, do_print)

