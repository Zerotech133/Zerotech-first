import React,{useEffect} from "react"
import { Modal,Select,Form,Input, message} from "antd"
import  '../combo.scss'
const { Option } = Select
type props = {
  visible: boolean,
  setVisible: (visible: boolean) => void,
  data: any,
  modifyCombo:any,
  combosList:any
}
export const ModifyCombos: React.FC<props> = ({visible,setVisible,data,combosList,modifyCombo}) => {
  // const [combosList, setCombosList] = useState<object[]>([]),Button
  const [form] = Form.useForm();
  useEffect(()=>{
    let allCombo:any=[]
    data.nodes.forEach((item:any)=>{
      if(item.comboId)allCombo.push(...item.comboId)
    })
    // let getAllComboId:any=new Set(allCombo)
    // let ComboIdArr=[...getAllComboId]
    // setCombosList(ComboIdArr)
  },[visible,data])
   // 关闭弹窗
  const handleAddCombosClose = () => {
    setVisible(false)
    form.resetFields();
  }
 const onFinish=()=>{
  form.validateFields().then((values:any)=>{
    let existSame=combosList.find((item:any)=>item===values.newComboName)
    if (existSame!==undefined) {
      message.warn(`已经存在${values.newComboName}集合`)
    }else{
      values.newComboName=values.newComboName.trim()
      modifyCombo(values)
      setVisible(false)
    }
    form.resetFields();
  }).catch(err=>{})
 }
  return (
    <>
      <Modal 
        title="修改集合名称"
        visible={visible}
        width={400}
        okText="修改"
        centered={true}
        bodyStyle={{ paddingBottom: '0'}}
        onCancel={handleAddCombosClose}
        onOk={onFinish}
        className="combos"
      >
        <Form
          form={form}
          onFinish={onFinish}>
            {/* 边类型 */}
            <Form.Item
              label="集合列表"
              name="oldComboNmae"
              rules={[{ required: true, message: '请先选中集合' }]}
            >
              <Select allowClear={true} placeholder='请选择要修改的集合'>
                  {
                    combosList.map((item:any,index:number)=>{
                      return (
                        <Option value={item} key={index}>{item}</Option>
                      )
                    })
                  }
              </Select>
            </Form.Item>
            <Form.Item 
              label="请输入新名称"
              name="newComboName"
              rules={[{ required: true, message: '集合名称不能为空' }]}
            >
              <Input style={{ width: '100%'}} placeholder="请输入新名称"/>
            </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
