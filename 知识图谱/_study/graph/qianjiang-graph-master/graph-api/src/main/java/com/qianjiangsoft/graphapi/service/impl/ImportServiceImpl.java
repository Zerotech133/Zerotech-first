package com.qianjiangsoft.graphapi.service.impl;

import cn.hutool.core.convert.Convert;
import cn.hutool.core.date.DateField;
import cn.hutool.core.date.DateUtil;
import cn.hutool.core.util.IdUtil;
import cn.hutool.core.util.ObjectUtil;
import cn.hutool.core.util.RandomUtil;
import cn.hutool.core.util.StrUtil;
import cn.hutool.json.JSONUtil;
import com.qianjiangsoft.graphapi.param.CreateEdgeParam;
import com.qianjiangsoft.graphapi.param.CreateVertexParam;
import com.qianjiangsoft.graphapi.service.DataService;
import com.qianjiangsoft.graphapi.service.ImportService;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.domain.entity.ExampleEdge;
import com.qianjiangsoft.graphcore.domain.entity.ExampleVertex;
import com.qianjiangsoft.graphcore.domain.entity.SchemaTemplate;
import com.qianjiangsoft.graphcore.mapper.ExampleEdgeMapper;
import com.qianjiangsoft.graphcore.mapper.ExampleVertexMapper;
import com.qianjiangsoft.graphcore.mapper.GraphTagMapper;
import com.qianjiangsoft.graphcore.mapper.SchemaTemplateMapper;
import com.qianjiangsoft.graphcore.model.*;
import com.qianjiangsoft.graphcore.service.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.text.SimpleDateFormat;
import java.util.*;
import java.util.stream.Collectors;

import static cn.hutool.core.date.DateField.DAY_OF_YEAR;

/**
 * @author marshallwu
 * @date 2022/4/12 16:16
 */
@Service
public class ImportServiceImpl implements ImportService {

    @Autowired
    ExampleEdgeMapper exampleEdgeMapper;

    @Autowired
    ExampleVertexMapper exampleVertexMapper;

    @Autowired
    SpaceService spaceService;

    @Autowired
    VertexService vertexService;

    @Autowired
    EdgeService edgeService;

    @Autowired
    SchemaService schemaService;

    @Autowired
    GraphTagMapper graphTagMapper;

    @Autowired
    SpaceSyncService spaceSyncService;

    @Autowired
    SchemaTemplateMapper schemaTemplateMapper;

    @Autowired
    DataService dataService;



    @Override
    public void importExampleData() {
        Space space = SpaceContextHolder.get();

        //清空数据
        spaceService.clearData(space.getId());

        //从模板导入schema
        importSchemaFromTemplate(1);

        //导入点
        List<ExampleVertex> exampleVertexList = exampleVertexMapper.selectList(null);
        List<CreateVertexParam> vertexParamList = new ArrayList<>();
        for (ExampleVertex exampleVertex : exampleVertexList) {
            Map attrMap = JSONUtil.parseObj(exampleVertex.getAttributes()).toBean(Map.class);
            CreateVertexParam vertex = new CreateVertexParam();
            vertex.setId(exampleVertex.getVid());
            vertex.setName(exampleVertex.getVid());
            vertex.setPropertyList(attrMap);
            vertexParamList.add(vertex);
        }
        dataService.createVertex(vertexParamList, "支付宝账号");

        //导入边
        List<ExampleEdge> edgeList = exampleEdgeMapper.selectList(null);
        List<CreateEdgeParam> edgeParamList = new ArrayList<>();
        for (ExampleEdge exampleEdge : edgeList) {
            Map attrMap = JSONUtil.parseObj(exampleEdge.getAttributes()).toBean(Map.class);

            CreateEdgeParam edgeParam = new CreateEdgeParam();
            edgeParam.setSourceVertexId(exampleEdge.getSrcId());
            edgeParam.setDestinationVertexId(exampleEdge.getDstId());
            edgeParam.setTime(RandomUtil.randomDate(DateUtil.date(), DateField.MONTH, -100, 100).toString());
            edgeParam.setPropertyList(attrMap);
            edgeParam.setStrength(RandomUtil.randomDouble(1, 1000));
            edgeParamList.add(edgeParam);
        }
        dataService.createEdge(edgeParamList, "账号交易");


        spaceSyncService.syncSpaceInfo(space.getId());
    }

    @Override
    public void importRandomTestData(Integer templateId,long sampleSize,int edgeNum,String spaceId)  {
        Space space = SpaceContextHolder.get();
        if(space==null){
            if(StrUtil.isNotEmpty(spaceId)){
                space = spaceService.selectSpaceById(spaceId);
            }else {
                space = spaceService.selectSpaceById("7cd29d3213be4b7d90d87f9b6e683350");
            }
            SpaceContextHolder.set(space);
//            space = graphEngine.getSpaceMap().get("7cd29d3213be4b7d90d87f9b6e683350");
//            SpaceContextHolder.setThreadLocal(space);
        }
        //清空数据
        spaceService.clearData(spaceId);
        //从模板导入schema
        importSchemaFromTemplate(templateId);
        testRandomInsert( sampleSize, space,"支付宝账号,银行卡号","账号交易", "4088,622",11,"zfb,yhk",edgeNum);
        testRandomInsert( sampleSize, space, "手机号","通话", "159",9,"th",edgeNum);

        //导入点
        /*Tag tag =  schemaService.findTagByName("支付宝", space.getId());

        SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss");
        List<Date> dates = randomDate("2021-1-1 00:00:00", sdf.format(new Date()), 100L,-1);
        List<String> ids=new ArrayList<>();
        Random rand = new Random();
        int strLength=11;
        for(long i=0;i<sampleSize;i++){
            Map<String, Object> attrMap = new HashMap<String, Object>();

            // 获得随机数
            double pross = (1 + rand.nextDouble()) * Math.pow(10, strLength);
            // 将获得的获得随机数转化为字符串
            String fixLengthString = String.valueOf(pross);
            // 返回固定的长度的随机数
            String id= "3088"+fixLengthString.substring(2, strLength + 1);
            ids.add(id);
            String name= "test"+fixLengthString.substring(2, strLength + 1);
            setPropertyMap(tag.getPropertyInfoList(),attrMap);
            vertexService.createVertex(tag, id, name, attrMap);
        }

        //导入边
        EdgeType edgeType =  schemaService.findEdgeTypeByName("支付宝", space.getId());
        for(long i=0;i<sampleSize;i++){
            String src=ids.get((int)i);
            List<String> idsNew=new ArrayList<>(ids);
            idsNew.remove(i);
            for(int j=0;j<10;j++){
                int m=(int)RandomUtil.randomLong(idsNew.size());
                String dsc=idsNew.get(m);
                idsNew.remove(m);
                int randNum = RandomUtil.randomInt(300);
                Date startDate=dates.get((int)i);
                String maxDate=sdf.format(startDate.getTime()+randNum*24*3600*1000);
                Map<String, Object> attrMap = new HashMap<String, Object>();
                setPropertyMap(edgeType.getPropertyInfoList(),attrMap);
                edgeService.createEdge(edgeType, null, src,
                        dsc, attrMap, RandomUtil.randomDouble(10, 10000),
                        RandomUtil.randomDate(DateUtil.date(), DateField.MONTH, -100, 100));
                if(idsNew.size()==0){
                    break;
                }
            }


        }*/
        dataService.syncSpaceInfo(space.getId());
    }

    public void testRandomInsert(long sampleSize,Space space,String tagTypeName,String edgeTypeName,String idStatic,int strLength,String idNameStatic,int edgeNum){
        List<String> ids=new ArrayList<>();
        Random rand = new Random();
        String[] tagName= tagTypeName.split(",");
        String[] idStaticSingle= idStatic.split(",");
        String[] idNameStaticSingle= idNameStatic.split(",");

        for(int t=0;t<tagName.length;t++) {
            Tag tag = schemaService.findTagByName(tagName[t], space.getId());
            for (long i = 0; i < sampleSize; i++) {
                Map<String, Object> attrMap = new HashMap<String, Object>();

                // 获得随机数
                /*double pross = (1 + rand.nextDouble()) * Math.pow(10, strLength);
                // 将获得的获得随机数转化为字符串
                String fixLengthString = String.valueOf(pross);
                // 返回固定的长度的随机数
                String id = idStaticSingle[t] + fixLengthString.substring(2, strLength + 1);*/
                String idRand=IdUtil.simpleUUID();
                String id =idStaticSingle[t] + idRand;
                ids.add(id);
//                String name = idNameStaticSingle[t] + fixLengthString.substring(2, strLength + 1);
                String name = idNameStaticSingle[t] +idRand;
                setPropertyMap(tag.getPropertyInfoList(), attrMap);
                vertexService.createVertex(tag, id, name, attrMap);
            }
        }
        //导入边
        SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss");
        int dl=ids.size();
        List<Date> dates = randomDate("2021-1-1 00:00:00", sdf.format(new Date()), dl,-1);
        EdgeType edgeType =  schemaService.findEdgeTypeByName(edgeTypeName, space.getId());
        List<CreateEdgeParam> edgeParamList=new ArrayList<>();
        for(long i=0;i<ids.size();i++){
            String src=ids.get((int)i);
            List<String> idsNew=new ArrayList<>(ids);
            idsNew.remove(src);
            for(int j=0;j<edgeNum;j++){
                int ms=idsNew.size()/2 ;
                if(idsNew.size()<=1){
                    continue;
                }
                int m=0;
                if(j%2==0){
                    m=(int)RandomUtil.randomLong(0,ms);
                }else {
                    m=(int)RandomUtil.randomLong(ms+1,idsNew.size());
                }
                String dsc=idsNew.get(m);
                idsNew.remove(dsc);
                int randEdgeNum = RandomUtil.randomInt(1,5);//边数量,两个点的边交易数据可以随机多个

                for(int en=0;en<randEdgeNum;en++) {
//                    int randNum = RandomUtil.randomInt(300);
//                    Date startDate = dates.get((int) i);
//                    String maxDate = sdf.format(startDate.getTime() + randNum * 24 * 3600 * 1000);
                    Map<String, Object> attrMap = new HashMap<String, Object>();
                    setPropertyMap(edgeType.getPropertyInfoList(), attrMap);
                    CreateEdgeParam edgeParam=new CreateEdgeParam();
                    edgeParam.setSourceVertexId(src);
                    edgeParam.setDestinationVertexId(dsc);
                    edgeParam.setPropertyList(attrMap);
                    edgeParam.setStrength( RandomUtil.randomDouble(10, 10000));
                    edgeParam.setTime(RandomUtil.randomDate(DateUtil.date(), DateField.MONTH, -100, 100).toString());
                    edgeParamList.add(edgeParam);
//                    edgeService.createEdge(edgeType, src,
//                            dsc, attrMap, RandomUtil.randomDouble(10, 10000),
//                            RandomUtil.randomDate(DateUtil.date(), DateField.MONTH, -100, 100));
                }

                if(idsNew.size()==0){
                    break;
                }
            }
        }
        dataService.createEdge(edgeParamList,edgeTypeName);
    }

    public void setPropertyMap(List<PropertyInfo> propertyInfoList,Map<String, Object> attrMap){
        for (PropertyInfo pi:propertyInfoList) {
            if(ObjectUtil.isNotEmpty(pi.getPropertyType())) {
//                attrMap.put(pi.getKey(), getPropertyValue(pi.getPropertyType().name));
                switch (pi.getPropertyType().name){
                    case "字符串":
                        attrMap.put(pi.getKey(),RandomUtil.randomString(10));
                        break;
                    case "日期":
                        attrMap.put(pi.getKey(),RandomUtil.randomDate(DateUtil.parse("2021-1-1"),DAY_OF_YEAR,1,300));
                        break;
                    case "数字":
                        attrMap.put(pi.getKey(),RandomUtil.randomNumbers(5));
                        break;
                    case "整数":
                        attrMap.put(pi.getKey(),Convert.toStr(RandomUtil.randomLong(10000))) ;
                        break;
                    default:
                        attrMap.put(pi.getKey(),null);
                        break;
                }
            }else {
                attrMap.put(pi.getKey(),null);
            }
        }
    }


    @Override
    public void importRandomTestDataStatic(long sampleSize)  {
        Space space = SpaceContextHolder.get();
        //清空数据
        spaceService.clearData(space.getId());
        //从模板导入schema
        importSchemaFromTemplate(1);

        //导入点
        Tag tag =  schemaService.findTagByName("支付宝账号", space.getId());

        SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss");
        List<Date> dates = randomDate("2021-1-1 00:00:00", sdf.format(new Date()), 100,-1);
        List<String> ids=new ArrayList<>();
        Random rand = new Random();
        int strLength=11;
        for(long i=0;i<sampleSize;i++){
            Map<String, Object> attrMap = new HashMap<String, Object>();

            // 获得随机数
            double pross = (1 + rand.nextDouble()) * Math.pow(10, strLength);
            // 将获得的获得随机数转化为字符串
            String fixLengthString = String.valueOf(pross);
            // 返回固定的长度的随机数
            String id= "3088"+fixLengthString.substring(2, strLength + 1);
            ids.add(id);
            String name= "test"+fixLengthString.substring(2, strLength + 1);

            attrMap.put("account_balances", null);
            attrMap.put("account_id", id);
            attrMap.put("account_type", null);
            attrMap.put("authentication_date", null);
            attrMap.put("authentication_type", null);
            attrMap.put("authentication_use_id", null);
            attrMap.put("bind_bank_card", null);
            attrMap.put("bind_phone", null);
            attrMap.put("corporate_name", null);
            attrMap.put("country", null);
            attrMap.put("email", null);
            attrMap.put("fixed_phone", null);
            attrMap.put("id", id);
            attrMap.put("is_main", "opposite");
            attrMap.put("login_email", null);
            attrMap.put("login_phone", null);
            attrMap.put("name", name);
            attrMap.put("phone", null);
            attrMap.put("register_date",null);
            attrMap.put("register_id", null);
            attrMap.put("status", null);
            attrMap.put("type", "alipay");

            vertexService.createVertex(tag, id, name, attrMap);
        }

        //导入边
        EdgeType edgeType =  schemaService.findEdgeTypeByName("账号交易", space.getId());
        for(long i=0;i<sampleSize;i++){
            String src=ids.get((int)i);
            List<String> idsNew=new ArrayList<>(ids);
            idsNew.remove(i);
            for(int j=0;j<10;j++){
                int m=(int)RandomUtil.randomLong(idsNew.size());
                String dsc=idsNew.get(m);
                idsNew.remove(m);
                int randNum = RandomUtil.randomInt(300);
                Date startDate=dates.get((int)i);
                String maxDate=sdf.format(startDate.getTime()+randNum*24*3600*1000);
                Map<String, Object> attrMap = new HashMap<String, Object>();
                attrMap.put("in_money", rand.nextInt(100000));
                attrMap.put("in_times", rand.nextInt(10));
                attrMap.put("max_date", maxDate);
                attrMap.put("min_date", sdf.format(startDate));
                attrMap.put("out_money", rand.nextInt(100000));
                attrMap.put("out_times", rand.nextInt(10));
                attrMap.put("total_money", rand.nextInt(100000));
                attrMap.put("total_times", rand.nextInt(10)+1);

                edgeService.createEdge(edgeType, src,
                        dsc, attrMap, RandomUtil.randomDouble(10, 10000),
                        RandomUtil.randomDate(DateUtil.date(), DateField.MONTH, -100, 100), null);
                if(idsNew.size()==0){
                    break;
                }
            }


        }
        spaceSyncService.syncSpaceInfo(space.getId());
    }

    private void importSchemaFromTemplate(Integer templateId){

        Space space = SpaceContextHolder.get();

        //查询模板
        SchemaTemplate schemaTemplate = schemaTemplateMapper.selectById(templateId);

        //清空tag和edge type
        schemaService.deleteTagBySpaceId(space.getId());
        schemaService.deleteEdgeTpeBySpaceId(space.getId());

        //导入测试tag和edge type
        List<Tag> tagList = JSONUtil.parseArray(schemaTemplate.getTagList()).toList(Tag.class);
        tagList.forEach(tag -> {
            schemaService.createTag(tag.getName(), tag.getPropertyInfoList(), null);
        });

        List<EdgeType> edgeTypeList = JSONUtil.parseArray(schemaTemplate.getEdgeTypeList()).toList(EdgeType.class);
        edgeTypeList.forEach(edgeType -> {
            schemaService.createEdgeType(edgeType.getName(), edgeType.getPropertyInfoList());
        });
    }

    /**
     * 生成size数量的随机时间，位于[start,end)范围内 时间倒序排列
     * @param start 开始时间
     * @param end 结束时间
     * @param size 生成时间个数
     * @param order 结果排序：-1时间倒叙，0 乱序，1时间正序
     * @return List<Date>
     * @throws Exception
     */
    public static List<Date> randomDate(String start, String end, int size, int order)  {
        List<Date> dates=null;
        try {
            SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss");
            Date startTime = sdf.parse(start);
            Date endTime = sdf.parse(end);

            Random random = new Random();
            dates = random.longs(size, startTime.getTime(), endTime.getTime()).mapToObj(t -> new Date(t)).collect(Collectors.toList());

            dates.sort((t1,t2)->{
                return t1.compareTo(t2) * order;
            });
//            return dates;
        }catch (Exception ex){

        }finally {
            return dates;
        }

    }
}
