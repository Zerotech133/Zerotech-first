import React, {useState} from "react"
import { Modal,Table,Input } from 'antd';

type props = {
  detailsVisible: boolean
  toParent: (data: any) => void
}
export const Detail: React.FC<props> = ({detailsVisible,toParent}) => {
  const [isModalVisible, setIsModalVisible] = useState(false);
  const { Search } = Input;

  const handleOk = () => {
    setIsModalVisible(false)
    toParent(isModalVisible)
  }
  
  const handleCancel = () => {
    setIsModalVisible(false)
    toParent(isModalVisible)
  }
  const columns = [
    {
      title: '序号',
      dataIndex: 'index',
      width: 150,
    },
    {
      title: '发送者',
      dataIndex: 'sender',
      width: 150,
    },
    {
      title: '接收者',
      dataIndex: 'receiver',
    },
    {
      title: '发送时间',
      dataIndex: 'time',
    },
    {
      title: '聊天内容',
      dataIndex: 'content',
    },
  ];
  
  const data = [];
  for (let i = 0; i < 100; i++) {
    data.push({
      key: i,
      index: `Edward King ${i}`,
      sender: '张三',
      receiver:'李四',
      time: `London, Park Lane no. ${i}`,
      content: `London, Park Lane no. ${i}`,
    })
  }

  const onSearch = (value:any) => console.log(value);

  return (
    <div className="detail-container">
      <Modal 
      title="聊天记录" 
      visible={ detailsVisible } 
      maskClosable={false}
      onOk={handleOk} 
      onCancel={handleCancel} 
      width="80%">
        聊天内容
        <Search placeholder="请输入关键字" onSearch={onSearch} enterButton="查询" style={{width:"200px",marginLeft:'10px',marginTop:
    '-5px'}}/>
        <Table columns={columns} dataSource={data} pagination={{ pageSize: 50 }} scroll={{ y: 240 }} />
      </Modal>
    </div>
  )
}
