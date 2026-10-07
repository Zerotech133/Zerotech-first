import React,{useEffect,useState} from "react"
import { Modal,Select,Form,Button} from "antd"
const { Option } = Select;
type props = {
  visible: boolean,
  setVisible: (visible: boolean) => void,
  deleteCombo:(deleteCombosList:any)=>void
  data:any,
  combosList:any
}
export const DeleteCombo: React.FC<props> = ({visible,setVisible,combosList,deleteCombo,data}) => {
  // const [combosList, setCombosList] = useState<any[]>([])
  const [deleteCombosList, setDeleteCombosList] = useState<any[]>([])
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
  const handleDeleteCombosClose = () => {
    setVisible(false)
    form.resetFields();
  }
  // 选择的要删除的集合
  const handleChange = (value: string[]) => {
    setDeleteCombosList(value)
  };
  const onFinish=()=>{
    deleteCombo(deleteCombosList)
    form.resetFields();
    setVisible(false)
  }
  return (
    <>
      <Modal 
        title="删除集合"
        visible={visible}
        footer={null}
        width={400}
        bodyStyle={{ paddingBottom: '0'}}
        centered={true}
        className="combos"
        onCancel={handleDeleteCombosClose}
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
            <Select
              mode="multiple"
              style={{ width: '100%' }}
              placeholder="请选择要删除的集合"
              onChange={handleChange}
              optionLabelProp="label"
            >
              {
                combosList.map((item:any,index:any)=>(
                  <Option value={item} label={item} key={index}>
                    <div className="demo-option-label-item">
                      {item}
                    </div>
                  </Option>
                ))
              }
            </Select>
          </Form.Item>
          <Form.Item wrapperCol={{ offset: 8, span: 16 }}>
            <Button size='middle' onClick={handleDeleteCombosClose}>取消</Button>
            <Button size='middle' type="primary" htmlType="submit" style={{ margin: '0 20px' }}>删除</Button>
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
