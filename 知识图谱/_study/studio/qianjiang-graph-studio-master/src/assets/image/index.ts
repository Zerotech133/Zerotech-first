import alipay from './alipay.svg'
import suspicion from './suspicion.svg'
import people from './suspicion.svg'
import telegram from './telegram.svg'
import bank from './bank.svg'
import phone from './phone.svg'
import email from './email.svg'

const Image = {
  alipay,
  suspicion,
  bank,
  telegram,
  phone,
  email,
  people
}

export type ImageType = keyof typeof Image

export default Image
